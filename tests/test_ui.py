import tempfile
import unittest
from pathlib import Path

from itbot.config import ROOT
from tests.fakes import FakeClient, reply, text, tool_use

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None

DRAFT = {"title": "Slow laptop", "description": "Slow since Monday",
         "category": "hardware", "priority": "normal"}


@unittest.skipIf(AppTest is None, "streamlit is not installed")
class UITests(unittest.TestCase):
    def start(self, *responses):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
        app.session_state["client_override"] = FakeClient(*responses)
        app.session_state["runtime_override"] = Path(folder.name)
        return app.run()

    def draft_a_ticket(self):
        app = self.start(reply(tool_use("draft_ticket", DRAFT)), reply(text("Please confirm.")))
        app.chat_input[0].set_value("open a ticket").run()
        self.assertFalse(app.exception)
        return app

    def test_confirm_button_files_the_ticket(self):
        app = self.draft_a_ticket()
        agent = app.session_state["agent"]
        self.assertEqual(len(agent.context.tickets.for_owner("E1001")), 1)
        next(b for b in app.button if b.label == "Confirm and file").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(agent.context.tickets.for_owner("E1001")), 2)
        self.assertNotIn("Confirm and file", [b.label for b in app.button])

    def test_discard_button_files_nothing(self):
        app = self.draft_a_ticket()
        next(b for b in app.button if b.label == "Discard").click().run()
        agent = app.session_state["agent"]
        self.assertEqual(len(agent.context.tickets.for_owner("E1001")), 1)

    def test_sidebar_settings_are_sent_with_the_message(self):
        app = self.start(reply(text("ok")))
        app.selectbox(key="model").select("claude-sonnet-5-5").run()
        app.selectbox(key="effort").select("low").run()
        app.toggle(key="thinking").set_value(False).run()
        app.text_area(key="instruction").input("Be brief.").run()
        app.chat_input[0].set_value("hello").run()
        self.assertFalse(app.exception)
        sent = app.session_state["client_override"].requests[0]
        self.assertEqual((sent["model"], sent["output_config"], sent["thinking"]),
                         ("claude-sonnet-5-5", {"effort": "low"}, {"type": "between_tools"}))
        self.assertIn("Be brief.", sent["system"])

    def test_thinking_is_locked_on_when_it_cannot_be_disabled(self):
        app = self.start()
        app.toggle(key="thinking").set_value(False).run()
        app.selectbox(key="effort").select("max").run()
        self.assertTrue(app.toggle(key="thinking_locked").disabled)
        self.assertTrue(app.session_state["agent"].settings.thinking)
        app.selectbox(key="effort").select("high").run()
        app.selectbox(key="model").select("claude-opus-5-5").run()
        self.assertTrue(app.toggle(key="thinking_locked").value)
        self.assertFalse(app.exception)

    def test_changing_a_setting_starts_a_new_conversation(self):
        app = self.start(reply(text("ok")))
        app.chat_input[0].set_value("hello").run()
        self.assertEqual(len(app.chat_message), 2)
        app.selectbox(key="effort").select("low").run()
        self.assertEqual((len(app.chat_message), app.session_state["agent"].messages), (0, []))

    def test_switching_user_starts_a_new_conversation(self):
        app = self.draft_a_ticket()
        app.selectbox[0].select("E1002").run()
        agent = app.session_state["agent"]
        self.assertEqual((agent.context.employee_id, agent.messages), ("E1002", []))
        self.assertEqual(len(app.chat_message), 0)


if __name__ == "__main__":
    unittest.main()
