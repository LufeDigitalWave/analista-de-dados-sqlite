"""Streamlit presentation for the SQLite data analyst.

Each question starts a fresh, bounded analysis. The UI renders only executed
results and operational steps; it does not contain business-specific SQL.
"""

import os
from pathlib import Path

import streamlit as st
from openai import OpenAI

from analista_dados.agent import AgentError, DataAnalyst
from analista_dados.database import SQLiteReadOnly
from analista_dados.model import OpenAIModel
from analista_dados.models import Answer, TraceStep
from analista_dados.visualization import prepare_visualization

EXAMPLES = (
    "Quais estados tiveram mais clientes distintos comprando via App em maio de 2025?",
    "Quantos clientes distintos interagiram com campanhas de WhatsApp em 2024?",
    "Como evoluíram as reclamações por canal nos últimos 12 meses dos dados?",
)


def database_path() -> Path:
    configured = Path(os.getenv("DATABASE_PATH", "data/anexo_desafio_1.db")).expanduser()
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parent / configured


def render_steps(steps: tuple[TraceStep, ...]) -> None:
    """Show tool activity, never the model's private reasoning."""
    for index, step in enumerate(steps, start=1):
        if step.action == "inspect_schema":
            st.markdown(f"**{index}. Esquema do banco consultado**")
            st.caption(step.purpose)
            continue

        st.markdown(f"**{index}. {step.purpose or 'Consulta SQL'}**")
        if step.sql:
            st.code(step.sql, language="sql")
        if step.error:
            st.error(f"Consulta rejeitada: {step.error}")
        elif step.row_count is not None:
            st.caption(f"{step.row_count} linha(s) retornada(s)")


def render_answer(answer: Answer) -> None:
    st.markdown("### Resposta")
    st.write(answer.text)
    if answer.assumptions:
        st.info(f"Interpretação usada: {answer.assumptions}")

    if answer.data is not None:
        st.markdown("### Dados consultados")
        choices = {
            "Automático": "auto",
            "Tabela": "table",
            "Barras": "bar",
            "Linha": "line",
        }
        selected = st.selectbox(
            "Visualização",
            options=list(choices),
            key="visualization_override",
            help="Um gráfico só aparece quando as colunas e os valores são adequados.",
        )
        view = prepare_visualization(answer.data, answer.chart, choices[selected])
        if view.kind == "bar":
            st.bar_chart(view.frame, x=view.x_column, y=list(view.y_columns), width="stretch")
        elif view.kind == "line":
            st.line_chart(view.frame, x=view.x_column, y=list(view.y_columns), width="stretch")
        elif selected in {"Barras", "Linha"}:
            st.caption(
                "Este resultado não tem colunas apropriadas para esse gráfico; veja a tabela."
            )
        st.dataframe(view.table, width="stretch", hide_index=True)
        if answer.data.truncated:
            st.warning("A tabela foi limitada a 200 linhas. Não use este recorte como totalização.")

    with st.expander("Ver consultas e passos", expanded=False):
        render_steps(answer.steps)


def main() -> None:
    st.set_page_config(
        page_title="Assistente de dados SQLite",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    path = database_path()
    has_database = path.is_file()
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    model_name = os.getenv("OPENAI_MODEL", "gpt-4.1")

    with st.sidebar:
        st.markdown("### Configuração")
        st.write(f"Banco: {'pronto' if has_database else 'não encontrado'}")
        st.write(f"Chave da API: {'configurada' if has_key else 'ausente'}")
        st.caption(f"Modelo: {model_name}")
        st.caption("Somente leitura · até 200 linhas por consulta")

    st.title("Assistente de dados")
    st.caption("Faça uma pergunta em português. Confira os dados e o SQL antes de usar a resposta.")
    st.caption("Cada pergunta é analisada separadamente; não há memória entre consultas.")

    ready = has_database and has_key
    if not has_database:
        st.warning("Banco não encontrado. Configure DATABASE_PATH para o arquivo SQLite local.")
    if not has_key:
        st.warning("Configure OPENAI_API_KEY no ambiente para fazer perguntas.")

    example_question = None
    if "answer" not in st.session_state and "error" not in st.session_state:
        st.markdown("### Experimente perguntar")
        for index, example in enumerate(EXAMPLES):
            if st.button(example, key=f"example-{index}", disabled=not ready, width="stretch"):
                example_question = example

    typed_question = st.chat_input("Pergunte sobre os dados…", disabled=not ready)
    question = typed_question or example_question
    if question:
        st.session_state.question = question
        st.session_state.pop("answer", None)
        st.session_state.pop("error", None)
        st.session_state.pop("visualization_override", None)
        with st.status("Consultando o banco…", expanded=False) as status:
            try:
                db = SQLiteReadOnly(path)
                model = OpenAIModel(OpenAI(), model=model_name)
                st.session_state.answer = DataAnalyst(db, model).ask(question)
            except (AgentError, ValueError) as exc:
                st.session_state.error = exc
                status.update(label="Não foi possível concluir", state="error")
            else:
                status.update(label="Análise concluída", state="complete")

    if "question" in st.session_state:
        st.markdown("### Pergunta")
        st.write(st.session_state.question)
    if "answer" in st.session_state:
        render_answer(st.session_state.answer)
    elif "error" in st.session_state:
        error = st.session_state.error
        st.error(str(error))
        if isinstance(error, AgentError) and error.steps:
            with st.expander("Ver consultas e passos", expanded=True):
                render_steps(error.steps)


if __name__ == "__main__":
    main()
