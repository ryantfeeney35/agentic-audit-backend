from langchain.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

insulation_prompt_bootstrap = ChatPromptTemplate.from_messages([
    ("system", "You are the Insulation Agent. Focus ONLY on insulation. "
               "Provide a short summary + follow-up questions."),
    ("user", "{context}")
])

insulation_prompt_followup = ChatPromptTemplate.from_messages([
    ("system", "You are the Insulation Agent. ONLY return NEW follow-up questions. "
               "If none remain, output exactly: 'No further insulation questions.'"),
    ("user", "{context}")
])

insulation_agent = {
    "bootstrap": insulation_prompt_bootstrap | ChatOpenAI(model="gpt-4.1", temperature=0.3),
    "followup": insulation_prompt_followup | ChatOpenAI(model="gpt-4.1", temperature=0.3),
}
