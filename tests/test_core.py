import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from evals.graders import grade_with_code
from itbot.config import (EFFORTS, MODELS, ROOT, Settings, estimate_cost,
                          thinking_can_be_disabled)
from itbot.factory import build_agent
from itbot.guardrails import redact, sanitize_output
from itbot.knowledge import KnowledgeBase
from itbot.request import FALLBACK_BETA, build_request
from itbot.tools import TOOLS, run_tool
from tests.fakes import FakeClient, reply, text, tool_use

DRAFT = {"title": "Slow laptop", "description": "Slow since Monday",
         "category": "hardware", "priority": "normal"}


class AgentTestCase(unittest.TestCase):
    def make(self, *responses, who="E1001", settings=Settings()):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        client = FakeClient(*responses)
        return client, build_agent(client, who, settings, runtime=Path(folder.name))


class SearchTests(unittest.TestCase):
    def test_expected_article_is_found(self):
        kb = KnowledgeBase(ROOT / "kb")
        for query, expected in [("forgot password", "KB-001"), ("vpn will not connect", "KB-002"),
                                ("new phone authenticator", "KB-003"), ("phishing email", "KB-004"),
                                ("lost laptop", "KB-005"), ("add printer", "KB-006"),
                                ("guest wi-fi", "KB-008"), ("shared drive access", "KB-010")]:
            with self.subTest(query=query):
                self.assertIn(expected, [a.id for a in kb.search(query)])

    def test_no_match_returns_nothing(self):
        self.assertEqual(KnowledgeBase(ROOT / "kb").search("zzzz qqqq"), [])


class GuardrailTests(unittest.TestCase):
    def test_secrets_are_redacted(self):
        for raw, secret in [("my password is Hunter2!!", "Hunter2"),
                            ("pwd: abc123xyz", "abc123xyz"),
                            ("key sk-ant-api03-abcdefghijklmnop", "sk-ant"),
                            ("card 4111 1111 1111 1111", "4111"),
                            ("ssn 123-45-6789", "123-45")]:
            with self.subTest(raw=raw):
                self.assertNotIn(secret, redact(raw)[0])

    def test_punctuation_inside_a_password_is_redacted(self):
        self.assertEqual(redact("my password is Hunter2!!, help")[0],
                         "my password is [REDACTED password], help")

    def test_ordinary_text_is_kept(self):
        for raw in ["my password is expired", "my password is not working.", "asset LH-2231"]:
            self.assertEqual(redact(raw)[0], raw)

    def test_outside_links_and_images_are_removed(self):
        cleaned = sanitize_output(
            "![x](https://evil.example/p.png) [go](https://evil.example/a?d=1) "
            "<https://evil.example> https://lumenharbor.example.evil.example/x "
            "https://reset.lumenharbor.example.")
        self.assertNotIn("evil.example", cleaned)
        self.assertIn("https://reset.lumenharbor.example", cleaned)


class ToolTests(AgentTestCase):
    def test_no_tool_accepts_an_identity(self):
        for tool in TOOLS:
            for name in tool["input_schema"]["properties"]:
                self.assertNotRegex(name, "employee|owner|user")

    def test_other_users_ticket_looks_missing(self):
        _, agent = self.make()
        theirs = run_tool("get_ticket", {"ticket_id": "LH-1002"}, agent.context)
        missing = run_tool("get_ticket", {"ticket_id": "LH-9999"}, agent.context)
        self.assertEqual(theirs, missing)
        listing, _ = run_tool("list_my_tickets", {}, agent.context)
        self.assertNotIn("LH-1002", listing)

    def test_bad_arguments_are_rejected(self):
        _, agent = self.make()
        for name, args in [("nope", {}), ("get_ticket", {}),
                           ("draft_ticket", {**DRAFT, "priority": "urgent"}),
                           ("get_ticket", {"ticket_id": "LH-1001", "owner": "E1002"})]:
            self.assertTrue(run_tool(name, args, agent.context)[1], (name, args))

    def test_draft_is_filed_only_on_confirm_and_only_once(self):
        _, agent = self.make()
        run_tool("draft_ticket", DRAFT, agent.context)
        store = agent.context.tickets
        self.assertEqual(len(store.for_owner("E1001")), 1)
        ticket = agent.confirm_draft("draft-1")
        self.assertEqual(ticket["id"], "LH-1003")
        self.assertEqual(len(store.for_owner("E1001")), 2)
        with self.assertRaises(ValueError):
            agent.confirm_draft("draft-1")


class AgentLoopTests(AgentTestCase):
    def test_tool_loop_returns_answer_with_sources(self):
        client, agent = self.make(
            reply(tool_use("search_knowledge_base", {"query": "reset password"})),
            reply(text("Use the reset page [KB-001].")))
        result = agent.send("forgot password")
        self.assertEqual((result.status, result.sources), ("ok", ["KB-001"]))
        sent = client.requests[1]["messages"]
        self.assertEqual(sent[-1]["content"][0]["tool_use_id"], "t1")
        self.assertEqual(result.usage["input_tokens"], 200)

    def test_parallel_results_go_back_in_one_message(self):
        client, agent = self.make(
            reply(tool_use("check_service_status", {}, "a"), tool_use("list_my_tickets", {}, "b")),
            reply(text("Done.")))
        agent.send("status?")
        self.assertEqual([r["tool_use_id"] for r in client.requests[1]["messages"][-1]["content"]],
                         ["a", "b"])

    def test_history_is_append_only(self):
        client, agent = self.make(reply(text("one")), reply(text("two")))
        agent.send("first")
        agent.send("second")
        first, second = client.requests
        self.assertEqual(second["messages"][:len(first["messages"])], first["messages"])
        self.assertEqual(first["system"], second["system"])
        self.assertEqual(first["tools"], second["tools"])

    def test_secret_never_reaches_the_api_or_log(self):
        client, agent = self.make(reply(text("Please don't share it.")))
        agent.send("my password is Hunter2!!")
        self.assertNotIn("Hunter2", json.dumps(client.requests[0]["messages"]))
        self.assertNotIn("Hunter2", agent.logger.path.read_text())

    def test_failed_turns_are_rolled_back(self):
        loop = [reply(tool_use("check_service_status", {})) for _ in range(6)]
        for status, responses in [("refusal", [reply(stop="refusal")]),
                                  ("truncated", [reply(text("par"), stop="max_tokens")]),
                                  ("empty_reply", [reply()]),
                                  ("tool_limit", loop)]:
            with self.subTest(status=status):
                _, agent = self.make(*responses)
                result = agent.send("hello")
                self.assertEqual(result.status, status)
                self.assertIn("4357", result.reply)
                self.assertEqual(agent.messages, [])

    def test_escalation_is_remembered_when_the_turn_fails(self):
        client, agent = self.make(
            reply(tool_use("escalate_to_human",
                           {"reason": "security_incident", "summary": "Clicked a link"})),
            reply(stop="refusal"), reply(text("ok")))
        self.assertIn("LH-1003", agent.send("I clicked a link").reply)
        agent.send("what now?")
        self.assertEqual(client.requests[-1]["messages"][-1]["role"], "system")
        self.assertIn("LH-1003", client.requests[-1]["messages"][-1]["content"])

    def test_confirmation_is_told_to_the_model_as_a_system_message(self):
        client, agent = self.make(reply(tool_use("draft_ticket", DRAFT)),
                                  reply(text("Please confirm.")), reply(text("Filed.")))
        agent.send("open a ticket")
        agent.confirm_draft("draft-1")
        agent.send("thanks")
        last = client.requests[-1]["messages"][-2:]
        self.assertEqual([m["role"] for m in last], ["user", "system"])
        self.assertEqual(last[0]["content"], "thanks")

    def test_api_error_reason_is_kept_for_debugging(self):
        try:
            import anthropic
            import httpx2
        except ImportError:
            self.skipTest("anthropic is not installed")
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        error = anthropic.BadRequestError(
            "bad request", response=httpx2.Response(400, request=request),
            body={"type": "error", "error": {"message": "Your credit balance is too low."}})

        def fail(**kwargs):
            raise error

        client, agent = self.make()
        client.messages.stream = fail
        result = agent.send("hello")
        self.assertEqual(result.status, "api_error")
        self.assertIn("credit balance is too low", result.flags[0])
        self.assertIn("credit balance is too low", agent.logger.path.read_text())
        self.assertEqual(agent.messages, [])

    def test_unknown_citation_is_flagged(self):
        _, agent = self.make(reply(text("See [KB-999].")))
        self.assertIn("KB-999", agent.send("hi").flags[0])

    def test_empty_and_oversize_input_make_no_api_call(self):
        client, agent = self.make()
        for value in ["  ", "x" * 4001]:
            self.assertEqual(agent.send(value).status, "rejected_input")
        self.assertEqual(client.requests, [])


class RequestTests(AgentTestCase):
    def test_every_model_effort_and_thinking_combination(self):
        for model, info in MODELS.items():
            for effort in EFFORTS:
                for thinking in (True, False):
                    settings = Settings(model=model, effort=effort, thinking=thinking)
                    with self.subTest(model=model, effort=effort, thinking=thinking):
                        if not thinking and not thinking_can_be_disabled(model, effort):
                            with self.assertRaises(ValueError):
                                build_request(settings, "s", [])
                            continue
                        sent = build_request(settings, "s", [])
                        self.assertEqual(sent["model"], model)
                        self.assertEqual(sent["output_config"], {"effort": effort})
                        self.assertEqual(sent["thinking"],
                                         {"type": "adaptive" if thinking else info.thinking_off})
                        self.assertEqual(sent.get("fallbacks"),
                                         "default" if info.fallbacks else None)

    def test_which_combinations_can_turn_thinking_off(self):
        self.assertTrue(thinking_can_be_disabled("claude-haiku-5-5", "high"))
        self.assertFalse(thinking_can_be_disabled("claude-haiku-5-5", "xhigh"))
        self.assertTrue(thinking_can_be_disabled("claude-sonnet-5-5", "low"))
        self.assertFalse(thinking_can_be_disabled("claude-opus-5-5", "low"))

    def test_chosen_settings_reach_the_api_with_the_message(self):
        settings = Settings(model="claude-sonnet-5-5", effort="low", thinking=False,
                            instruction="Answer in French.")
        client, agent = self.make(reply(text("Bonjour")), settings=settings)
        result = agent.send("hello")
        sent = client.requests[0]
        self.assertEqual(client.endpoints, ["beta"])
        self.assertEqual((sent["model"], sent["thinking"], sent["output_config"], sent["betas"]),
                         ("claude-sonnet-5-5", {"type": "between_tools"}, {"effort": "low"},
                          [FALLBACK_BETA]))
        self.assertIn("Answer in French.", sent["system"])
        self.assertIn("Safety:", sent["system"])  # built-in rules are kept
        self.assertEqual(sent["messages"][-1], {"role": "user", "content": "hello"})
        self.assertEqual(result.request["user_message"], "hello")
        self.assertEqual(result.request["thinking"], {"type": "between_tools"})

    def test_haiku_uses_the_standard_endpoint_and_no_instruction_adds_nothing(self):
        client, agent = self.make(reply(text("hi")))
        agent.send("hello")
        self.assertEqual(client.endpoints, ["standard"])
        self.assertNotIn("additional_instructions", client.requests[0]["system"])

    def test_secret_in_system_instruction_is_redacted(self):
        _, agent = self.make(settings=Settings(instruction="password: Hunter2!!"))
        self.assertNotIn("Hunter2", agent.system)

    def test_tool_calls_before_a_fallback_switch_are_not_run_or_replayed(self):
        switch = NS(type="fallback")
        first = reply(tool_use("escalate_to_human", {"reason": "other", "summary": "x"}, "old"),
                      switch, tool_use("check_service_status", {}, "new"))
        client, agent = self.make(first, reply(text("Done.")),
                                  settings=Settings(model="claude-opus-5-5"))
        result = agent.send("hi")
        self.assertEqual([c["name"] for c in result.tool_calls], ["check_service_status"])
        self.assertEqual(agent.context.escalations, [])
        self.assertEqual([b.type for b in client.requests[1]["messages"][1]["content"]],
                         ["fallback", "tool_use"])
        self.assertIn("fallback model", result.flags[0])


class CostAndGraderTests(unittest.TestCase):
    def test_cost_uses_the_long_prompt_rate_above_100k(self):
        self.assertAlmostEqual(estimate_cost("claude-haiku-5-5", {"input_tokens": 100_000}), 0.01)
        self.assertAlmostEqual(estimate_cost("claude-haiku-5-5", {"input_tokens": 200_000}), 0.10)
        self.assertIsNone(estimate_cost("some-other-model", {}))
        million = {"cache_read_input_tokens": 1_000_000}
        self.assertAlmostEqual(estimate_cost("claude-opus-5-5", million), 0.20)
        self.assertAlmostEqual(estimate_cost("claude-sonnet-5-5", {"output_tokens": 1_000_000}), 10)

    def test_graders_catch_failures(self):
        expect = {"calls": ["a"], "no_calls": ["b"], "cites": ["KB-001"],
                  "not_contains": ["salary"], "secret": "s3cret"}
        self.assertEqual(len(grade_with_code(expect, ["b"], "salary", "s3cret")), 5)

    def test_case_file_is_well_formed(self):
        cases = json.loads((ROOT / "evals" / "cases.json").read_text())
        names = {t["name"] for t in TOOLS}
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for case in cases:
            expect = case["expect"]
            self.assertTrue(set(expect.get("calls", []) + expect.get("no_calls", [])) <= names)


if __name__ == "__main__":
    unittest.main()
