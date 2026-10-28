import asyncio
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin
from weakref import WeakKeyDictionary

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.knowledge import NO_ANSWER, format_voice_knowledge_result
from app.vector_store import search_knowledge

load_dotenv()

SYSTEM_PROMPT = (
    "You are Nova, a friendly and professional India-based telecom customer-support "
    "assistant. Use search_plans to compare available plans, get_plan_details for a "
    "specific plan, check_account_balance only after the customer provides their "
    "10-digit Indian mobile number, check_network_status for coverage or 4G/5G "
    "questions, recommend_plan when the customer describes their usage, and "
    "escalate_to_support for unresolved issues. Use search_telecom_knowledge for "
    "other telecom FAQs. Only report facts returned by tools; never invent plans, "
    "prices, balances, coverage, outages, or a support ticket. Quote Indian prices "
    "in rupees (₹), without converting currencies. Clearly distinguish published "
    "coverage information from a live network-status check. If a required system "
    "is not connected, say that clearly and offer the configured support contact. "
    "Never request passwords, one-time codes, or full payment-card details. Do not "
    "repeat the customer's phone number in your response. Keep answers short and "
    "conversational, especially for voice calls."
)

_agent: Any = None
_agents_by_loop: WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = WeakKeyDictionary()
INDIAN_MOBILE_PATTERN = re.compile(r"^[6-9]\d{9}$")


class LangChainNotConfiguredError(RuntimeError):
    pass


class TelecomIntegrationError(RuntimeError):
    pass


@tool
async def search_telecom_knowledge(question: str) -> str:
    """Search the Qdrant telecom support knowledge base for verified answers."""
    return await asyncio.to_thread(format_voice_knowledge_result, question)


def _search_category(question: str, categories: set[str], limit: int = 5) -> list[dict[str, Any]]:
    return search_knowledge(question, limit=limit, categories=categories)


def _format_matches(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "No verified matching information was found in the knowledge base."

    excerpts = []
    for match in matches:
        metadata = match["metadata"]
        source = metadata.get("source", "knowledge base")
        page = metadata.get("page")
        citation = f"{source}, page {page}" if page else source
        content = metadata.get("answer") or match["text"]
        question = metadata.get("question")
        heading = f"{question}\n" if question else ""
        excerpts.append(f"{heading}{content}\nSource: {citation}")
    return "\n\n".join(excerpts)


@tool
async def search_plans(query: str) -> str:
    """Find current, verified telecom plan options and prices in the knowledge base."""
    matches = await asyncio.to_thread(
        _search_category, query, {"Plan", "Plans", "Postpaid", "Tariff"}
    )
    return _format_matches(matches)


@tool
async def get_plan_details(plan_name: str) -> str:
    """Look up the verified price, validity, data, and terms for a named plan."""
    matches = await asyncio.to_thread(
        _search_category,
        f"Plan details price validity data benefits for {plan_name}",
        {"Plan", "Plans", "Postpaid", "Tariff"},
    )
    return _format_matches(matches)


@tool
async def recommend_plan(usage_pattern: str) -> str:
    """Recommend among documented plans using the customer's stated usage pattern."""
    matches = await asyncio.to_thread(
        _search_category,
        f"Plan recommendation for this usage: {usage_pattern}",
        {"Plan", "Plans", "Postpaid", "Tariff"},
    )
    if not matches:
        return (
            "No verified plan catalog is available to make a recommendation. "
            "Do not guess a plan or price; ask the customer to check the official "
            "plan list or contact support."
        )
    return (
        "Compare these verified options against the customer's stated usage. "
        "Do not infer missing prices, data allowances, or terms.\n\n"
        f"{_format_matches(matches)}"
    )


def _provider_api_url(path: str) -> str:
    base_url = os.getenv("TELECOM_API_URL")
    if not base_url:
        raise TelecomIntegrationError(
            "Live account and network systems are not connected. "
            "Contact the provider through its official app or support channel."
        )
    return urljoin(f"{base_url.rstrip('/')}/", path)


async def _provider_post(path: str, payload: dict[str, str]) -> dict[str, Any]:
    headers = {}
    api_key = os.getenv("TELECOM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                _provider_api_url(path),
                json=payload,
                headers=headers,
            )
        except httpx.RequestError as error:
            raise TelecomIntegrationError(
                f"The provider {path} service could not be reached."
            ) from error
    if response.is_error:
        raise TelecomIntegrationError(
            f"The provider {path} service returned HTTP {response.status_code}."
        )
    try:
        result = response.json()
    except ValueError as error:
        raise TelecomIntegrationError(
            f"The provider {path} service returned invalid JSON."
        ) from error
    if not isinstance(result, dict):
        raise TelecomIntegrationError(
            f"The provider {path} service returned an invalid response."
        )
    return result


@tool
async def check_account_balance(phone_number: str) -> str:
    """Check an account balance using a 10-digit Indian mobile number (6-9 prefix)."""
    if not INDIAN_MOBILE_PATTERN.fullmatch(phone_number.strip()):
        return "Enter a valid 10-digit Indian mobile number beginning with 6, 7, 8, or 9."

    result = await _provider_post(
        "balance",
        {"phone_number": phone_number.strip()},
    )
    balance = result.get("balance")
    if not isinstance(balance, (int, float, str)) or isinstance(balance, bool):
        raise TelecomIntegrationError(
            "The provider balance response did not include a valid balance."
        )
    try:
        amount = Decimal(str(balance))
    except InvalidOperation as error:
        raise TelecomIntegrationError(
            "The provider balance response did not include a valid balance."
        ) from error
    if not amount.is_finite() or amount < 0 or result.get("currency", "INR") != "INR":
        raise TelecomIntegrationError(
            "The provider returned an unsupported balance or currency."
        )

    return f"Current prepaid balance: ₹{amount:,.2f}."


@tool
async def check_network_status(location: str) -> str:
    """Check live network status or coverage for a named Indian area or city."""
    area = location.strip()
    if not area or len(area) > 120:
        return "Provide a city, area, or PIN code to check network status."

    if os.getenv("TELECOM_API_URL"):
        result = await _provider_post("network", {"location": area})
        status = result.get("status")
        if not isinstance(status, str) or not status.strip():
            raise TelecomIntegrationError(
                "The provider network response did not include a status."
            )
        technology = result.get("technology")
        detail = f" Technology: {technology}." if isinstance(technology, str) else ""
        message = result.get("message")
        detail += f" {message.strip()}" if isinstance(message, str) and message.strip() else ""
        coverage = result.get("coverage")
        detail += f" Coverage: {coverage.strip()}." if isinstance(coverage, str) and coverage.strip() else ""
        return f"Provider network status for {area}: {status.strip()}.{detail}"

    matches = await asyncio.to_thread(
        search_knowledge,
        f"Network coverage and signal information for {area}",
        5,
        {"Network"},
    )
    if not matches:
        return (
            "A live network-status service is not connected and the knowledge base "
            f"has no verified coverage information for {area}. Check the provider's "
            "official coverage map or contact support."
        )
    return (
        "This is published support information, not a live outage check. "
        f"{_format_matches(matches)}"
    )


@tool
async def escalate_to_support(issue_summary: str) -> str:
    """Create a support escalation for an unresolved issue without including secrets."""
    summary = issue_summary.strip()
    if not summary or len(summary) > 1000:
        return "Provide a brief issue summary of at most 1000 characters to request support."

    webhook_url = os.getenv("TELECOM_ESCALATION_URL")
    if not webhook_url:
        support_contact = os.getenv("SUPPORT_CONTACT")
        if support_contact:
            return (
                "No support ticket was created because an escalation service is not "
                f"connected. Contact the provider at: {support_contact}"
            )
        return (
            "No support ticket was created because an escalation service and support "
            "contact are not configured. Contact the provider through its official app."
        )

    safe_summary = re.sub(
        r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)",
        "[phone number removed]",
        summary,
    )
    headers = {}
    api_key = os.getenv("TELECOM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                webhook_url,
                json={"issue_summary": safe_summary},
                headers=headers,
            )
        except httpx.RequestError as error:
            raise TelecomIntegrationError(
                "The support escalation service could not be reached."
            ) from error
    if response.is_error:
        raise TelecomIntegrationError(
            f"The support escalation service returned HTTP {response.status_code}."
        )
    try:
        result = response.json()
    except ValueError as error:
        raise TelecomIntegrationError(
            "The support escalation service returned invalid JSON."
        ) from error
    if not isinstance(result, dict) or not isinstance(result.get("ticket_id"), str):
        raise TelecomIntegrationError(
            "The escalation service response did not confirm a support ticket."
        )
    return f"Support ticket created successfully. Reference: {result['ticket_id']}."


def _create_agent() -> Any:
    openai_api_key = os.getenv("OPENAI_API_KEY")
    groq_api_key = os.getenv("GROQ_API_KEY")
    if openai_api_key:
        model_name = os.getenv("LANGCHAIN_MODEL", "gpt-4o-mini")
        api_key = openai_api_key
        base_url = os.getenv("OPENAI_BASE_URL") or None
    elif groq_api_key:
        model_name = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        api_key = groq_api_key
        base_url = os.getenv(
            "GROQ_BASE_URL",
            "https://api.groq.com/openai/v1",
        )
    else:
        raise LangChainNotConfiguredError(
            "LangChain chat is not configured. Set OPENAI_API_KEY or "
            "GROQ_API_KEY in .env."
        )
    return create_agent(
        model=ChatOpenAI(
            model=model_name,
            api_key=api_key,
            temperature=1e-8 if groq_api_key and not openai_api_key else 0,
            base_url=base_url,
        ),
        tools=[
            search_telecom_knowledge,
            search_plans,
            get_plan_details,
            recommend_plan,
            check_account_balance,
            check_network_status,
            escalate_to_support,
        ],
        system_prompt=SYSTEM_PROMPT,
        name="nova_telecom_support",
    )


def _get_agent() -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        global _agent
        if _agent is None:
            _agent = _create_agent()
        return _agent

    agent = _agents_by_loop.get(loop)
    if agent is None:
        agent = _create_agent()
        _agents_by_loop[loop] = agent
    return agent


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


async def answer_question(question: str) -> tuple[str, str | None]:
    result = await _get_agent().ainvoke(
        {"messages": [{"role": "user", "content": question}]}
    )
    messages = result.get("messages", [])
    tool_was_used = any(isinstance(message, ToolMessage) for message in messages)
    if not tool_was_used:
        return NO_ANSWER, None

    final_messages = [message for message in messages if isinstance(message, AIMessage)]
    answer = _message_text(final_messages[-1].content) if final_messages else ""
    if not answer.strip():
        raise RuntimeError("The LangChain agent did not return a final answer.")

    tool_results = [
        _message_text(message.content)
        for message in messages
        if isinstance(message, ToolMessage)
    ]
    if all(NO_ANSWER in result for result in tool_results):
        return NO_ANSWER, None

    used_tools = [
        message.name
        for message in messages
        if isinstance(message, ToolMessage) and message.name
    ]
    source = (
        "Support tools: " + ", ".join(dict.fromkeys(name.replace("_", " ") for name in used_tools))
        if tool_results
        else None
    )
    return answer, source
