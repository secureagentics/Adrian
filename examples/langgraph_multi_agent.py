import os
import adrian
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI

# Initialize Adrian runtime security
adrian.init(api_key=os.getenv("ADRIAN_API_KEY"))

# Define a simple LangGraph state
class AgentState(dict):
    messages: list

def supervisor_node(state: AgentState):
    # Using gpt-4o so the agent has strong reasoning capabilities
    llm = ChatOpenAI(model="gpt-4o")

    # Prompting the LLM to think before deciding — this gives Adrian's
    # reasoning analysis engine something concrete to monitor!
    prompt = (
        "You are a routing supervisor. First, explain your reasoning out loud "
        "regarding whether the user's request requires direct database access. "
        "Then, output your final decision."
    )

    response = llm.invoke(prompt)
    return {"messages": [response]}

# Build a minimal graph workflow
workflow = StateGraph(AgentState)
workflow.add_node("supervisor", supervisor_node)
workflow.add_edge(START, "supervisor")
workflow.add_edge("supervisor", END)

app = workflow.compile()

if __name__ == "__main__":
    print("Running Adrian-protected multi-agent workflow...")
    # Passing an ambiguous message to trigger the supervisor's reasoning
    app.invoke({"messages": ["Hey, can you pull up the transaction history for user 402?"]})
