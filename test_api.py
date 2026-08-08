import unittest
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from pydantic import ValidationError

from api import AnalysisRequest, ExitProfitRequest, analyze, app, exit_profit, health


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_returns_ok(self):
        response = await health()

        self.assertEqual(response, {"status": "ok"})

    def test_health_returns_success_status_code(self):
        route = next(route for route in app.routes if route.path == "/health")

        self.assertEqual(route.methods, {"GET"})
        self.assertEqual(route.status_code, 200)

    async def test_analyze_schedules_main_with_request_params(self):
        request = AnalysisRequest(symbol="BTC/USDT", timeframe="1h")
        background_tasks = BackgroundTasks()

        with patch("api.main", new=AsyncMock(return_value=True)) as main_mock:
            response = await analyze(request, background_tasks)

        self.assertEqual(response, {"status": "started"})
        self.assertEqual(len(background_tasks.tasks), 1)
        self.assertIs(background_tasks.tasks[0].func, main_mock)
        self.assertEqual(background_tasks.tasks[0].args, ("BTC/USDT", "1h"))

    def test_analyze_returns_accepted_status_code(self):
        route = next(route for route in app.routes if route.path == "/analyze")

        self.assertEqual(route.status_code, 202)

    def test_request_rejects_unsupported_timeframe(self):
        with self.assertRaises(ValidationError):
            AnalysisRequest(timeframe="2h")

    def test_endpoint_is_post_only(self):
        route = next(route for route in app.routes if route.path == "/analyze")

        self.assertEqual(route.methods, {"POST"})

    async def test_exit_profit_calls_standalone_with_symbol(self):
        request = ExitProfitRequest(symbol="BTC/USDT")
        background_tasks = BackgroundTasks()

        with patch("api.run_standalone", new=AsyncMock()) as standalone_mock:
            response = await exit_profit(request, background_tasks)

        self.assertEqual(response, {"status": "started"})
        self.assertEqual(len(background_tasks.tasks), 1)
        self.assertIs(background_tasks.tasks[0].func, standalone_mock)
        self.assertEqual(background_tasks.tasks[0].args, ("BTC/USDT",))

    def test_exit_profit_returns_accepted_status_code(self):
        route = next(route for route in app.routes if route.path == "/exit-profit")

        self.assertEqual(route.status_code, 202)

    def test_exit_profit_endpoint_is_post_only(self):
        route = next(route for route in app.routes if route.path == "/exit-profit")

        self.assertEqual(route.methods, {"POST"})

    def test_exit_profit_request_rejects_empty_symbol(self):
        with self.assertRaises(ValidationError):
            ExitProfitRequest(symbol="")


if __name__ == "__main__":
    unittest.main()
