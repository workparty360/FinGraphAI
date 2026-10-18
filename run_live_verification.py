import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

from dotenv import load_dotenv

load_dotenv(".env")

from execution_tracer import get_traces_for_request
from falkor_memory import FalkorMemory


class DummyEmbeddings:
    def embed_query(self, text: str):
        return [0.0] * 384


from financial_graph import get_financial_graph
from memory_agents import FinanceAgentTeam

financial_graph = get_financial_graph()
print(f"FalkorDB available: {financial_graph.is_available}")

memory = FalkorMemory(
    "live_test_ws",
    "live_test_user",
    DummyEmbeddings(),
    graph=financial_graph._graph if financial_graph.is_available else None,
)
team = FinanceAgentTeam(memory=memory)

thread_apple = "thread_live_apple_001"

print("\n" + "=" * 60)
print("TEST 1: General Financial Question: What is return on equity?")
print("=" * 60)
res1 = team.ask("What is return on equity?", thread_id="thread_general")
print("Answer:\n", res1.get("answer"))
t1 = get_traces_for_request(res1.get("request_id", ""))
print(f"\nTraces recorded for TEST 1: {len(t1)}")
for trace in t1:
    print(f"  [{trace['stage']}] {trace['agent_name']} status={trace['status']}")

print("\n" + "=" * 60)
print("TEST 2: Exact Net Income: What was Apple's net income in fiscal 2023?")
print("=" * 60)
res2 = team.ask("What was Apple's net income in fiscal 2023?", thread_id=thread_apple)
print("Answer:\n", res2.get("answer"))
t2 = get_traces_for_request(res2.get("request_id", ""))
print(f"\nTraces recorded for TEST 2: {len(t2)}")
for trace in t2:
    print(f"  [{trace['stage']}] {trace['agent_name']} status={trace['status']}")

print("\n" + "=" * 60)
print("TEST 3: Best Year: Which year was Apple's best performing year?")
print("=" * 60)
res3 = team.ask("Which year was Apple's best performing year?", thread_id=thread_apple)
print("Answer:\n", res3.get("answer"))

print("\n" + "=" * 60)
print("TEST 4: Comparison: Compare Microsoft and Apple.")
print("=" * 60)
res4 = team.ask("Compare Microsoft and Apple.", thread_id="thread_comparison")
print("Answer:\n", res4.get("answer"))

print("\n" + "=" * 60)
print("TEST 5: Multi-turn Follow-up: What about 2024? (on Apple thread)")
print("=" * 60)
res5 = team.ask("What about 2024?", thread_id=thread_apple)
print("Answer:\n", res5.get("answer"))

print("\n" + "=" * 60)
print("TEST 6: Fresh Chat Unspecified Follow-up: What about 2024?")
print("=" * 60)
thread_fresh = "thread_fresh_new_chat_002"
res6 = team.ask("What about 2024?", thread_id=thread_fresh)
print("Answer:\n", res6.get("answer"))

print("\n" + "=" * 60)
print("TEST 7: Switching Back to Apple Thread: What was the operating margin?")
print("=" * 60)
res7 = team.ask("What was the operating margin?", thread_id=thread_apple)
print("Answer:\n", res7.get("answer"))
