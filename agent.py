import asyncio

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, RunContext, function_tool, inference

from app.langchain_agent import (
    check_account_balance as balance_lookup,
    check_network_status as network_lookup,
    escalate_to_support as support_escalation,
    get_plan_details as plan_details_lookup,
    recommend_plan as plan_recommendation,
    search_plans as plan_search,
    search_telecom_knowledge as knowledge_search,
)
from app.vector_store import ensure_knowledge_base_ingested

load_dotenv()


class TelecomSupportAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly telecom customer-support voice assistant. "
                "For factual telecom support questions, use the appropriate support "
                "tool before answering; do not guess from memory. Use search_knowledge "
                "for telecom FAQs, search_plans or get_plan_details for plan options, "
                "recommend_plan for usage-based recommendations, check_balance only "
                "after the caller provides a 10-digit Indian mobile number, "
                "check_network_status for coverage or outages, and "
                "escalate_support for unresolved issues. Only state what tool results "
                "confirm. Say clearly when live account, network, or escalation systems "
                "are not connected. Use Indian rupees (₹) for prices and amounts, and "
                "never repeat a customer's phone number. Never request passwords, "
                "one-time codes, or full payment-card details. If a request is unclear, "
                "ask one short clarifying question. Keep replies concise and conversational."
            )
        )

    @function_tool()
    async def search_knowledge(self, _context: RunContext, question: str) -> str:
        """Find verified answers to general telecom support questions."""
        return await knowledge_search.ainvoke({"question": question})

    @function_tool()
    async def search_plans(self, _context: RunContext, query: str) -> str:
        """Find verified telecom plans and prices in the knowledge base."""
        return await plan_search.ainvoke({"query": query})

    @function_tool()
    async def get_plan_details(self, _context: RunContext, plan_name: str) -> str:
        """Look up verified price, validity, data, and terms for a named plan."""
        return await plan_details_lookup.ainvoke({"plan_name": plan_name})

    @function_tool()
    async def recommend_plan(self, _context: RunContext, usage_pattern: str) -> str:
        """Compare documented plans against the customer's stated usage."""
        return await plan_recommendation.ainvoke({"usage_pattern": usage_pattern})

    @function_tool()
    async def check_balance(self, _context: RunContext, phone_number: str) -> str:
        """Check an account balance using the customer's 10-digit mobile number."""
        return await balance_lookup.ainvoke({"phone_number": phone_number})

    @function_tool()
    async def check_network_status(self, _context: RunContext, location: str) -> str:
        """Check live provider network status or published coverage information."""
        return await network_lookup.ainvoke({"location": location})

    @function_tool()
    async def escalate_support(self, _context: RunContext, issue_summary: str) -> str:
        """Request support escalation for an unresolved issue."""
        return await support_escalation.ainvoke({"issue_summary": issue_summary})


server = AgentServer()


@server.rtc_session(agent_name="telecom-support")
async def telecom_support(ctx: agents.JobContext) -> None:
    await asyncio.to_thread(ensure_knowledge_base_ingested)
    session = AgentSession(
        llm=inference.RealtimeModel(
            model="openai/gpt-realtime-2.1",
            voice="marin",
        )
    )
    await session.start(room=ctx.room, agent=TelecomSupportAgent())
    await session.generate_reply(
        instructions="Greet the customer and ask how you can help with their telecom service."
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
