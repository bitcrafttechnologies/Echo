from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from echo import Action, EchoNodeWebSocketHost, Entity, MockProvider, ProviderRouter, Runtime, RuntimeService, Signal
from echo.host import register_user_message_handler
from medulla_node import EchoConnectionConfig, MedullaNodeConfig, MedullaNodeRuntime
from medulla_node.adapters.macbook import MacbookAdapter, MacbookLocation
from medulla_node.adapters.web import DEFAULT_RESEARCH_DOMAINS, WebResearchAdapter, _BingSearchExtractor


class FakeMacbookBackend:
    def battery(self):
        return {"available": True, "percent": 87, "charging": False, "power_source": "battery"}

    def health(self):
        return {"platform": "macOS-test", "cpu_count": 10, "load_average": [0.1, 0.2, 0.3], "disk_free_bytes": 42}

    def now(self):
        return datetime(2026, 9, 13, 8, 30, tzinfo=timezone(timedelta(hours=-7)))

    def weather(self, latitude, longitude):
        return {"latitude": latitude, "longitude": longitude, "current": {"temperature_2m": 31}, "source": "open-meteo.com"}


class FakeWebBackend:
    def search(self, query, limit):
        return [
            {"url": "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms/", "title": "MIT Algorithms"},
            {"url": "https://evil.invalid/tracker", "title": "Filtered"},
            {"url": "https://arxiv.org/abs/1706.03762", "title": "Attention Is All You Need"},
        ]

    def fetch(self, url, maximum_bytes):
        return {"url": url, "title": "Open source", "content": "bounded research content", "content_type": "text/html", "truncated": False}


def node_config(node_id: str, endpoint: str) -> MedullaNodeConfig:
    return MedullaNodeConfig(
        node_id=node_id,
        display_name={"macbook-home": "Home MacBook", "open-web": "Open Web Research"}[node_id],
        node_type="computer" if node_id == "macbook-home" else "service",
        echo=EchoConnectionConfig(endpoint=endpoint, heartbeat_interval=0.02, heartbeat_timeout=1.0, reconnect_initial_delay=0.02, reconnect_max_delay=0.05),
        metadata={"implementation": f"{node_id}-medulla-node", "version": "0.1"},
    )


class DesktopAndWebNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_search_fallback_extracts_only_result_links(self) -> None:
        parser = _BingSearchExtractor()
        parser.feed(
            '<script><a href="https://evil.invalid/script">ignored</a></script>'
            '<li class="b_algo"><h2><a href="https://ocw.mit.edu/courses/6-006/">'
            'Introduction to <strong>Algorithms</strong></a></h2></li>'
        )
        self.assertEqual(
            parser.results,
            [{"url": "https://ocw.mit.edu/courses/6-006/", "title": "Introduction to Algorithms"}],
        )

    async def test_macbook_node_is_scoped_and_emits_telemetry(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "note.txt"
            note.write_text("Echo can read this bounded file.", encoding="utf-8")
            config = MedullaNodeConfig(node_id="macbook-test")
            runtime = MedullaNodeRuntime(config)
            runtime.register_adapter(MacbookAdapter(config.provider, file_roots=(root,), location=MacbookLocation(latitude=33.4484, longitude=-112.074, label="Phoenix"), telemetry_interval=0.01, backend=FakeMacbookBackend()))
            await runtime.start()
            try:
                from medulla_protocol import NodeAction
                listed = await runtime.execute(
                    NodeAction(type="filesystem.list", resource_id="filesystem.root.0")
                )
                self.assertEqual(
                    [item["name"] for item in listed.result["entries"]], ["note.txt"]
                )
                read = await runtime.execute(NodeAction(type="filesystem.read", parameters={"path": str(note)}))
                self.assertEqual(read.result["content"], "Echo can read this bounded file.")
                denied = await runtime.execute(NodeAction(type="filesystem.read", parameters={"path": "/etc/hosts"}))
                self.assertEqual(denied.state.value, "failed")
                kinds = {(await asyncio.wait_for(runtime.receive_signal(), 1)).signal_type for _ in range(3)}
                self.assertEqual(kinds, {"battery.telemetry", "system.health.telemetry", "system.datetime.telemetry"})
            finally:
                await runtime.stop()

    async def test_web_policy_filters_search_and_rejects_unapproved_urls(self) -> None:
        config = MedullaNodeConfig(node_id="web-test")
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(WebResearchAdapter(config.provider, backend=FakeWebBackend()))
        from medulla_protocol import NodeAction
        await runtime.start()
        try:
            result = await runtime.execute(NodeAction(type="web.search", parameters={"query": "algorithms", "limit": 5}))
            self.assertEqual([item["title"] for item in result.result["results"]], ["MIT Algorithms", "Attention Is All You Need"])
            self.assertEqual(result.result["search_scope"], "public_web")
            self.assertIn("do not define", result.result["access_policy"]["behavior"])
            denied = await runtime.execute(NodeAction(type="web.fetch", parameters={"url": "http://127.0.0.1/private"}))
            self.assertEqual(denied.state.value, "failed")
            lookalike = await runtime.execute(NodeAction(type="web.fetch", parameters={"url": "https://github.com.evil.invalid/private"}))
            self.assertEqual(lookalike.state.value, "failed")
            self.assertIn("github.com", DEFAULT_RESEARCH_DOMAINS)
            self.assertIn("openstax.org", DEFAULT_RESEARCH_DOMAINS)
        finally:
            await runtime.stop()

    async def test_echo_manually_approves_both_nodes_then_uses_their_data(self) -> None:
        echo_runtime = Runtime([Entity("echo")])
        host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla", signal_target=echo_runtime)
        await host.start()
        service = RuntimeService(echo_runtime, node_host=host)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "life.txt"
            note.write_text("A small window into the computer.", encoding="utf-8")
            mac_config = node_config("macbook-home", host.endpoint)
            web_config = node_config("open-web", host.endpoint)
            mac = MedullaNodeRuntime(mac_config)
            mac.register_adapter(MacbookAdapter(mac_config.provider, file_roots=(root,), location=MacbookLocation(latitude=33.4484, longitude=-112.074), telemetry_interval=0.02, backend=FakeMacbookBackend()))
            web = MedullaNodeRuntime(web_config)
            web.register_adapter(WebResearchAdapter(web_config.provider, backend=FakeWebBackend()))
            try:
                await mac.start()
                await web.start()
                self.assertTrue(await host.wait_for_node("macbook-home", 2))
                self.assertTrue(await host.wait_for_node("open-web", 2))
                candidates = service.get_medulla_nodes()
                self.assertEqual({item["negotiation_state"] for item in candidates}, {"awaiting_approval"})
                self.assertEqual(host.registry.inspect(), ())

                for node_id in ("macbook-home", "open-web"):
                    approved = await service.decide_medulla_node(node_id, "approve", "explicit operator approval")
                    self.assertEqual(approved["negotiation_state"], "approved")
                    active = await service.authorize_medulla_node(node_id, {})
                    self.assertEqual(active["negotiation_state"], "active")

                battery = await host.execute("macbook-home", Action(type="battery.status"))
                clock = await host.execute("macbook-home", Action(type="system.datetime"))
                location = await host.execute("macbook-home", Action(type="location.current"))
                weather = await host.execute("macbook-home", Action(type="weather.current"))
                file_data = await host.execute("macbook-home", Action(type="filesystem.read", parameters={"path": str(note)}))
                search = await host.execute("open-web", Action(type="web.search", parameters={"query": "algorithms"}))
                page = await host.execute("open-web", Action(type="web.fetch", parameters={"url": "https://github.com/python/cpython"}))
                self.assertEqual(battery.result["percent"], 87)
                self.assertIn("2026-09-13", clock.result["iso8601"])
                self.assertEqual(location.result["latitude"], 33.4484)
                self.assertEqual(weather.result["source"], "open-meteo.com")
                self.assertIn("window", file_data.result["content"])
                self.assertEqual(search.result["results"][0]["title"], "MIT Algorithms")
                self.assertEqual(page.result["content"], "bounded research content")
                self.assertTrue(any(item.name == "web.search" for item in host.registry.inspect()))

                provider = MockProvider(response="Your battery is 87%, and I found the MIT course.")
                register_user_message_handler(echo_runtime.get_entity("echo"), ProviderRouter(remote=provider), node_host=host)
                await echo_runtime.emit(Signal(type="UserMessage", payload={"text": "Check my battery and search for algorithms"}, metadata={"search_query": "algorithms"}))
                observations = provider.requests[-1].context["medulla_observations"]
                self.assertEqual([item["capability"] for item in observations], ["battery.status", "web.search"])
                self.assertEqual(observations[0]["result"]["percent"], 87)
                self.assertEqual(observations[1]["result"]["results"][0]["title"], "MIT Algorithms")
            finally:
                await mac.stop()
                await web.stop()
                await host.stop()
                echo_runtime.stop()


if __name__ == "__main__":
    unittest.main()
