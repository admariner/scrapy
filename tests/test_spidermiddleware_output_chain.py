from testfixtures import LogCapture

from scrapy import Request, Spider
from scrapy.utils.defer import deferred_f_from_coro_f, maybe_deferred_to_future
from scrapy.utils.test import get_crawler
from tests.mockserver.http import MockServer


class LogExceptionMiddleware:
    def process_spider_exception(self, response, exception, spider):
        spider.logger.info(
            "Middleware: %s exception caught", exception.__class__.__name__
        )


# ================================================================================
# (0) recover from an exception on a spider callback
class RecoveryMiddleware:
    def process_spider_exception(self, response, exception, spider):
        spider.logger.info(
            "Middleware: %s exception caught", exception.__class__.__name__
        )
        return [
            {"from": "process_spider_exception"},
            Request(response.url, meta={"dont_fail": True}, dont_filter=True),
        ]


class RecoverySpider(Spider):
    name = "RecoverySpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES_BASE": {},
        "SPIDER_MIDDLEWARES": {
            RecoveryMiddleware: 10,
        },
    }

    async def start(self):
        yield Request(self.mockserver.url("/status?n=200"))

    def parse(self, response):
        yield {"test": 1}
        self.logger.info("DONT_FAIL: %s", response.meta.get("dont_fail"))
        if not response.meta.get("dont_fail"):
            raise TabError


class RecoveryAsyncGenSpider(RecoverySpider):
    name = "RecoveryAsyncGenSpider"

    async def parse(self, response):
        for r in super().parse(response):
            yield r


# ================================================================================
# (1) exceptions from a spider middleware's process_spider_input method
class FailProcessSpiderInputMiddleware:
    def process_spider_input(self, response, spider):
        spider.logger.info("Middleware: will raise IndexError")
        raise IndexError


class ProcessSpiderInputSpiderWithoutErrback(Spider):
    name = "ProcessSpiderInputSpiderWithoutErrback"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            # spider
            FailProcessSpiderInputMiddleware: 8,
            LogExceptionMiddleware: 6,
            # engine
        }
    }

    async def start(self):
        yield Request(url=self.mockserver.url("/status?n=200"), callback=self.parse)

    def parse(self, response):
        return {"from": "callback"}


class ProcessSpiderInputSpiderWithErrback(ProcessSpiderInputSpiderWithoutErrback):
    name = "ProcessSpiderInputSpiderWithErrback"

    async def start(self):
        yield Request(
            self.mockserver.url("/status?n=200"), self.parse, errback=self.errback
        )

    def errback(self, failure):
        self.logger.info("Got a Failure on the Request errback")
        return {"from": "errback"}


# ================================================================================
# (2) exceptions from a spider callback (generator)
class GeneratorCallbackSpider(Spider):
    name = "GeneratorCallbackSpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            LogExceptionMiddleware: 10,
        },
    }

    async def start(self):
        yield Request(self.mockserver.url("/status?n=200"))

    def parse(self, response):
        yield {"test": 1}
        yield {"test": 2}
        raise ImportError


class AsyncGeneratorCallbackSpider(GeneratorCallbackSpider):
    async def parse(self, response):
        yield {"test": 1}
        yield {"test": 2}
        raise ImportError


# ================================================================================
# (2.1) exceptions from a spider callback (generator, middleware right after callback)
class GeneratorCallbackSpiderMiddlewareRightAfterSpider(GeneratorCallbackSpider):
    name = "GeneratorCallbackSpiderMiddlewareRightAfterSpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            LogExceptionMiddleware: 100000,
        },
    }


# ================================================================================
# (3) exceptions from a spider callback (not a generator)
class NotGeneratorCallbackSpider(Spider):
    name = "NotGeneratorCallbackSpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            LogExceptionMiddleware: 10,
        },
    }

    async def start(self):
        yield Request(self.mockserver.url("/status?n=200"))

    def parse(self, response):
        return [{"test": 1}, {"test": 1 / 0}]


# ================================================================================
# (3.1) exceptions from a spider callback (not a generator, middleware right after callback)
class NotGeneratorCallbackSpiderMiddlewareRightAfterSpider(NotGeneratorCallbackSpider):
    name = "NotGeneratorCallbackSpiderMiddlewareRightAfterSpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            LogExceptionMiddleware: 100000,
        },
    }


# ================================================================================
# (4) exceptions from a middleware process_spider_output method (generator)
class _GeneratorDoNothingMiddleware:
    def process_spider_output(self, response, result, spider):
        for r in result:
            r["processed"].append(f"{self.__class__.__name__}.process_spider_output")
            yield r

    def process_spider_exception(self, response, exception, spider):
        method = f"{self.__class__.__name__}.process_spider_exception"
        spider.logger.info("%s: %s caught", method, exception.__class__.__name__)


class GeneratorFailMiddleware:
    def process_spider_output(self, response, result, spider):
        for r in result:
            r["processed"].append(f"{self.__class__.__name__}.process_spider_output")
            yield r
            raise LookupError

    def process_spider_exception(self, response, exception, spider):
        method = f"{self.__class__.__name__}.process_spider_exception"
        spider.logger.info("%s: %s caught", method, exception.__class__.__name__)
        yield {"processed": [method]}


class GeneratorDoNothingAfterFailureMiddleware(_GeneratorDoNothingMiddleware):
    pass


class GeneratorRecoverMiddleware:
    def process_spider_output(self, response, result, spider):
        for r in result:
            r["processed"].append(f"{self.__class__.__name__}.process_spider_output")
            yield r

    def process_spider_exception(self, response, exception, spider):
        method = f"{self.__class__.__name__}.process_spider_exception"
        spider.logger.info("%s: %s caught", method, exception.__class__.__name__)
        yield {"processed": [method]}


class GeneratorDoNothingAfterRecoveryMiddleware(_GeneratorDoNothingMiddleware):
    pass


class GeneratorOutputChainSpider(Spider):
    name = "GeneratorOutputChainSpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            GeneratorFailMiddleware: 10,
            GeneratorDoNothingAfterFailureMiddleware: 8,
            GeneratorRecoverMiddleware: 5,
            GeneratorDoNothingAfterRecoveryMiddleware: 3,
        },
    }

    async def start(self):
        yield Request(self.mockserver.url("/status?n=200"))

    def parse(self, response):
        yield {"processed": ["parse-first-item"]}
        yield {"processed": ["parse-second-item"]}


# ================================================================================
# (5) exceptions from a middleware process_spider_output method (not generator)


class _NotGeneratorDoNothingMiddleware:
    def process_spider_output(self, response, result, spider):
        out = []
        for r in result:
            r["processed"].append(f"{self.__class__.__name__}.process_spider_output")
            out.append(r)
        return out

    def process_spider_exception(self, response, exception, spider):
        method = f"{self.__class__.__name__}.process_spider_exception"
        spider.logger.info("%s: %s caught", method, exception.__class__.__name__)


class NotGeneratorFailMiddleware:
    def process_spider_output(self, response, result, spider):
        out = []
        for r in result:
            r["processed"].append(f"{self.__class__.__name__}.process_spider_output")
            out.append(r)
        raise ReferenceError

    def process_spider_exception(self, response, exception, spider):
        method = f"{self.__class__.__name__}.process_spider_exception"
        spider.logger.info("%s: %s caught", method, exception.__class__.__name__)
        return [{"processed": [method]}]


class NotGeneratorDoNothingAfterFailureMiddleware(_NotGeneratorDoNothingMiddleware):
    pass


class NotGeneratorRecoverMiddleware:
    def process_spider_output(self, response, result, spider):
        out = []
        for r in result:
            r["processed"].append(f"{self.__class__.__name__}.process_spider_output")
            out.append(r)
        return out

    def process_spider_exception(self, response, exception, spider):
        method = f"{self.__class__.__name__}.process_spider_exception"
        spider.logger.info("%s: %s caught", method, exception.__class__.__name__)
        return [{"processed": [method]}]


class NotGeneratorDoNothingAfterRecoveryMiddleware(_NotGeneratorDoNothingMiddleware):
    pass


class NotGeneratorOutputChainSpider(Spider):
    name = "NotGeneratorOutputChainSpider"
    custom_settings = {
        "SPIDER_MIDDLEWARES": {
            NotGeneratorFailMiddleware: 10,
            NotGeneratorDoNothingAfterFailureMiddleware: 8,
            NotGeneratorRecoverMiddleware: 5,
            NotGeneratorDoNothingAfterRecoveryMiddleware: 3,
        },
    }

    async def start(self):
        yield Request(self.mockserver.url("/status?n=200"))

    def parse(self, response):
        return [
            {"processed": ["parse-first-item"]},
            {"processed": ["parse-second-item"]},
        ]


# ================================================================================
class TestSpiderMiddleware:
    mockserver: MockServer

    @classmethod
    def setup_class(cls):
        cls.mockserver = MockServer()
        cls.mockserver.__enter__()

    @classmethod
    def teardown_class(cls):
        cls.mockserver.__exit__(None, None, None)

    async def crawl_log(self, spider: type[Spider]) -> LogCapture:
        crawler = get_crawler(spider)
        with LogCapture() as log:
            await maybe_deferred_to_future(crawler.crawl(mockserver=self.mockserver))
        return log

    @deferred_f_from_coro_f
    async def test_recovery(self):
        """
        (0) Recover from an exception in a spider callback. The final item count should be 3
        (one yielded from the callback method before the exception is raised, one directly
        from the recovery middleware and one from the spider when processing the request that
        was enqueued from the recovery middleware)
        """
        log = await self.crawl_log(RecoverySpider)
        assert "Middleware: TabError exception caught" in str(log)
        assert str(log).count("Middleware: TabError exception caught") == 1
        assert "'item_scraped_count': 3" in str(log)

    @deferred_f_from_coro_f
    async def test_recovery_asyncgen(self):
        """
        Same as test_recovery but with an async callback.
        """
        log = await self.crawl_log(RecoveryAsyncGenSpider)
        assert "Middleware: TabError exception caught" in str(log)
        assert str(log).count("Middleware: TabError exception caught") == 1
        assert "'item_scraped_count': 3" in str(log)

    @deferred_f_from_coro_f
    async def test_process_spider_input_without_errback(self):
        """
        (1.1) An exception from the process_spider_input chain should be caught by the
        process_spider_exception chain from the start if the Request has no errback
        """
        log1 = await self.crawl_log(ProcessSpiderInputSpiderWithoutErrback)
        assert "Middleware: will raise IndexError" in str(log1)
        assert "Middleware: IndexError exception caught" in str(log1)

    @deferred_f_from_coro_f
    async def test_process_spider_input_with_errback(self):
        """
        (1.2) An exception from the process_spider_input chain should not be caught by the
        process_spider_exception chain if the Request has an errback
        """
        log1 = await self.crawl_log(ProcessSpiderInputSpiderWithErrback)
        assert "Middleware: IndexError exception caught" not in str(log1)
        assert "Middleware: will raise IndexError" in str(log1)
        assert "Got a Failure on the Request errback" in str(log1)
        assert "{'from': 'errback'}" in str(log1)
        assert "{'from': 'callback'}" not in str(log1)
        assert "'item_scraped_count': 1" in str(log1)

    @deferred_f_from_coro_f
    async def test_generator_callback(self):
        """
        (2) An exception from a spider callback (returning a generator) should
        be caught by the process_spider_exception chain. Items yielded before the
        exception is raised should be processed normally.
        """
        log2 = await self.crawl_log(GeneratorCallbackSpider)
        assert "Middleware: ImportError exception caught" in str(log2)
        assert "'item_scraped_count': 2" in str(log2)

    @deferred_f_from_coro_f
    async def test_async_generator_callback(self):
        """
        Same as test_generator_callback but with an async callback.
        """
        log2 = await self.crawl_log(AsyncGeneratorCallbackSpider)
        assert "Middleware: ImportError exception caught" in str(log2)
        assert "'item_scraped_count': 2" in str(log2)

    @deferred_f_from_coro_f
    async def test_generator_callback_right_after_callback(self):
        """
        (2.1) Special case of (2): Exceptions should be caught
        even if the middleware is placed right after the spider
        """
        log21 = await self.crawl_log(GeneratorCallbackSpiderMiddlewareRightAfterSpider)
        assert "Middleware: ImportError exception caught" in str(log21)
        assert "'item_scraped_count': 2" in str(log21)

    @deferred_f_from_coro_f
    async def test_not_a_generator_callback(self):
        """
        (3) An exception from a spider callback (returning a list) should
        be caught by the process_spider_exception chain. No items should be processed.
        """
        log3 = await self.crawl_log(NotGeneratorCallbackSpider)
        assert "Middleware: ZeroDivisionError exception caught" in str(log3)
        assert "item_scraped_count" not in str(log3)

    @deferred_f_from_coro_f
    async def test_not_a_generator_callback_right_after_callback(self):
        """
        (3.1) Special case of (3): Exceptions should be caught
        even if the middleware is placed right after the spider
        """
        log31 = await self.crawl_log(
            NotGeneratorCallbackSpiderMiddlewareRightAfterSpider
        )
        assert "Middleware: ZeroDivisionError exception caught" in str(log31)
        assert "item_scraped_count" not in str(log31)

    @deferred_f_from_coro_f
    async def test_generator_output_chain(self):
        """
        (4) An exception from a middleware's process_spider_output method should be sent
        to the process_spider_exception method from the next middleware in the chain.
        The result of the recovery by the process_spider_exception method should be handled
        by the process_spider_output method from the next middleware.
        The final item count should be 2 (one from the spider callback and one from the
        process_spider_exception chain)
        """
        log4 = await self.crawl_log(GeneratorOutputChainSpider)
        assert "'item_scraped_count': 2" in str(log4)
        assert (
            "GeneratorRecoverMiddleware.process_spider_exception: LookupError caught"
            in str(log4)
        )
        assert (
            "GeneratorDoNothingAfterFailureMiddleware.process_spider_exception: LookupError caught"
            in str(log4)
        )
        assert (
            "GeneratorFailMiddleware.process_spider_exception: LookupError caught"
            not in str(log4)
        )
        assert (
            "GeneratorDoNothingAfterRecoveryMiddleware.process_spider_exception: LookupError caught"
            not in str(log4)
        )
        item_from_callback = {
            "processed": [
                "parse-first-item",
                "GeneratorFailMiddleware.process_spider_output",
                "GeneratorDoNothingAfterFailureMiddleware.process_spider_output",
                "GeneratorRecoverMiddleware.process_spider_output",
                "GeneratorDoNothingAfterRecoveryMiddleware.process_spider_output",
            ]
        }
        item_recovered = {
            "processed": [
                "GeneratorRecoverMiddleware.process_spider_exception",
                "GeneratorDoNothingAfterRecoveryMiddleware.process_spider_output",
            ]
        }
        assert str(item_from_callback) in str(log4)
        assert str(item_recovered) in str(log4)
        assert "parse-second-item" not in str(log4)

    @deferred_f_from_coro_f
    async def test_not_a_generator_output_chain(self):
        """
        (5) An exception from a middleware's process_spider_output method should be sent
        to the process_spider_exception method from the next middleware in the chain.
        The result of the recovery by the process_spider_exception method should be handled
        by the process_spider_output method from the next middleware.
        The final item count should be 1 (from the process_spider_exception chain, the items
        from the spider callback are lost)
        """
        log5 = await self.crawl_log(NotGeneratorOutputChainSpider)
        assert "'item_scraped_count': 1" in str(log5)
        assert (
            "GeneratorRecoverMiddleware.process_spider_exception: ReferenceError caught"
            in str(log5)
        )
        assert (
            "GeneratorDoNothingAfterFailureMiddleware.process_spider_exception: ReferenceError caught"
            in str(log5)
        )
        assert (
            "GeneratorFailMiddleware.process_spider_exception: ReferenceError caught"
            not in str(log5)
        )
        assert (
            "GeneratorDoNothingAfterRecoveryMiddleware.process_spider_exception: ReferenceError caught"
            not in str(log5)
        )
        item_recovered = {
            "processed": [
                "NotGeneratorRecoverMiddleware.process_spider_exception",
                "NotGeneratorDoNothingAfterRecoveryMiddleware.process_spider_output",
            ]
        }
        assert str(item_recovered) in str(log5)
        assert "parse-first-item" not in str(log5)
        assert "parse-second-item" not in str(log5)
