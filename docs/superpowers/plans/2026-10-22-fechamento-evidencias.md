# Fechamento verificável do Desafio 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produzir uma amostra privada e auditável de Q1–Q5, corrigir o falso `REVIEW` de datas completas da Q2 e publicar apenas código/documentação que reflita a revisão efetivamente feita.

**Architecture:** Preservar `DataAnalyst`, `SQLiteReadOnly` e o avaliador conservador. `evals.live_eval` continua sendo a CLI; um módulo pequeno e isolado grava um JSON novo em `work/private-review/` somente com `--private-review`. Neste workspace Codex, essa pasta fica no `work/` da tarefa, fora de `outputs/` e da árvore Git; em clone comum, cai no `<repo>/work/` ignorado. Resposta, SQL selecionado, histórico de SQLs bem-sucedidos e veredito já estão juntos no ponto de captura de `run_evaluation`.

**Tech Stack:** Python 3.12, SQLite, OpenAI API existente, pytest, Ruff, Streamlit e Playwright CLI; nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-10-22-fechamento-evidencias-design.md`

## Global Constraints

- Apenas o Desafio 1. Não mexer no agente, prompt, banco de produção, UI, deploy ou Desafio 2 sem defeito novo reproduzido.
- Banco e chave permanecem nas fontes privadas originais e **não** entram no pacote. Resposta literal, SQL literal, hash do banco e matriz humana ficam somente em `work/`; nenhum desses dados entra em Git, ZIP ou mensagem pública.
- A CLI sem `--private-review` mantém o stdout redigido e não cria pacote. A opção não aceita caminho arbitrário, não sobrescreve arquivo anterior e falha fechada se não puder gravar. O destino resolvido fica fora de `outputs/` neste workspace; fallback em clone comum permanece sob `<repo>/work/`.
- Pacote: formato, UTC, commit/árvore limpa antes e depois, modelo, hash do banco antes/depois, casos selecionados/tentados/concluídos, `error_code` da execução e `integrity_issues` separados; por caso, somente os campos permitidos pela spec. Erro tratado usa `null` para resposta indisponível; preflight inválido não cria arquivo.
- Captura real de cinco casos exige commit local e árvore limpa, no máximo uma rodada antes de reavaliar. A autorização anterior cobre pergunta/esquema/SQL/agregados na API, não envio do `.db`.
- No fallback de clone comum, `<repo>/work/` precisa permanecer ignorado. Neste workspace, o pacote deve estar fora do repo e de `outputs/`; conferir arquivos rastreados e manifesto do ZIP antes da publicação.
- TDD: RED observado antes da implementação, GREEN e controles; avaliação `REVIEW` não vira `PASS` por inferência retrospectiva.
- O usuário dispensou Strix para este case. Gates: pytest, Ruff, `pip check`, revisão do diff, scan local de segredos e smoke reproduzível Playwright. No Windows restrito, `--basetemp` deve apontar para `..\..\work\<nome-novo>` a partir da raiz do repo.

## Mapa de arquivos e interfaces

- `evals/live_eval.py`: altera somente interpretação numérica da Q2 e integração opt-in; `run_evaluation(..., private_review=False, model_name="unknown") -> int` preserva os chamadores existentes.
- `evals/private_review.py` (novo): `prepare_private_review() -> None`, `git_state() -> tuple[str, bool]`, `sha256_file(path: Path) -> str`, `write_private_review(payload: dict[str, object]) -> Path`. Diretório fixo derivado da posição do repo: `<task>/work` se seu pai for `outputs`, `<repo>/work` caso contrário; nenhum argumento CLI de destino.
- `tests/test_live_eval.py`: regressão Q2 e integração sintética com o agente/SQLite real e um modelo roteirizado, sem API paga.
- `tests/test_private_review.py` (novo): serialização, escrita exclusiva, recusa de saída fora de `work/`, metadados sem segredos e falha antes de gravar JSON inválido.
- `README.md` e `docs/STATUS-ENTREGA.md`: apenas resultado público verificável, sem respostas ou SQL literais da rodada real.
- `<task>/work/private-review/*.json` e `*.md` neste workspace: pacote imutável e matriz humana separada, ambos fora do repositório; fallback `<repo>/work/private-review` permanece ignorado.

---

### Task 1: Corrigir o falso `REVIEW` da Q2 sem afrouxar o gate

**Files:** Modify `evals/live_eval.py:224-232`; Test `tests/test_live_eval.py` perto dos testes da Q2.

**Interfaces:** `assess_case(2, answer, reference, selected_sql)` não muda. `_text_integer_verdict(text, expected, exclude_years=True)` reconhece as duas bordas do ano civil 2024 nos formatos `DD/MM/YYYY` e `YYYY-MM-DD`; qualquer data parcial, errada ou intervalo incompleto continua conservador.

- [x] **Step 1: Escrever testes RED com oráculo independente.** Para `REFERENCES[1] == ((17,),)` e `SQL[1]`, a frase “Foram 17 clientes distintos entre 01/01/2024 e 31/12/2024.” deve ser `PASS`; “17 clientes em 2025”, “17 clientes entre 02/01/2024 e 31/12/2024”, “17 clientes, dos quais 3...”, “17 clientes em 01/01/2024 e 31/12/2024”, “17 clientes, mas não entre 01/01/2024 e 31/12/2024” e “18 clientes entre 01/01/2024 e 31/12/2024” não podem ser `PASS`; “dezessete clientes” permanece `REVIEW`.

```python
answer = _answer(2, text="Foram 17 clientes distintos entre 01/01/2024 e 31/12/2024.")
assert assess_case(2, answer, REFERENCES[1], SQL[1]).status == "PASS"
```

- [x] **Step 2: Executar os testes novos e registrar RED.** Run: `.venv\Scripts\python.exe -m pytest tests/test_live_eval.py -q --basetemp ..\..\work\pytest-q2-red-20261022 --tb=short`. O caso positivo e o controle de `2025` falharam contra o código anterior; depois, duas frases com datas isoladas e uma com negação do intervalo reproduziram falsos `PASS` antes dos ajustes contextuais.
- [x] **Step 3: Implementar a menor regra contextual.** Reconhecer datas completas do texto, normalizá-las e mascará-las somente quando as duas bordas, nesta ordem, são `2024-01-01` e `2024-12-31` **e** formam intervalo explícito, não negado (`entre ... e ...` ou `de ... a/até ...`); um intervalo inválido, negado ou datas listadas retorna `ambiguous`. Excluir o ano 2024 isolado da lista de contagens, não o 2025. Manter 17 como contagem exigida e números extras como `ambiguous`; ponto final não deve esconder um inteiro, mas sufixo decimal deve.

```python
_Q2_FULL_DATE = re.compile(r"(?<!\d)(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})(?!\d)")


def _normalize_q2_date(raw: str) -> str:
    if "/" in raw:
        day, month, year = raw.split("/")
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return raw


dates = list(_Q2_FULL_DATE.finditer(folded))
if dates:
    if [_normalize_q2_date(m.group()) for m in dates] != ["2024-01-01", "2024-12-31"]:
        return "ambiguous"
    # Validar "entre ... e ..." ou "de ... a/até ..." e ausência de negação
    # na mesma oração ANTES de ocultar os números das datas.
    if not intervalo_explicito_positivo(dates, folded):
        return "ambiguous"
without_dates = _Q2_FULL_DATE.sub(" ", folded) if dates else folded
numbers = [
    int(n)
    for n in re.findall(r"(?<![\d,.])[+-]?\d+(?!\d|[,.]\d)", without_dates)
    if not exclude_years or n != "2024"
]
```

- [x] **Step 4: Executar GREEN e regressões da Q2.** Os 24 testes focados da Q2 passaram após os contraexemplos de datas isoladas e negação; a suíte completa passou no gate da Task 2.
- [ ] **Step 5: Revisar o diff estreito da Q2 e incorporar no commit local da Task 2.** Ambas as tasks alteram `evals/live_eval.py` e `tests/test_live_eval.py` no mesmo checkout; um commit coeso após os dois gates evita estagiar parcialmente arquivos em edição. O commit não publica dados privados.

### Task 2: Captura privada e opt-in da avaliação

**Files:** Create `evals/private_review.py`, `tests/test_private_review.py`; Modify `evals/live_eval.py:1022-1103`, `tests/test_live_eval.py`.

**Interfaces:** `prepare_private_review()` rejeita symlink/junction que escape da pasta fixa e roda antes de qualquer chamada paga quando a opção está ativa. `write_private_review(payload)` valida JSON por completo antes de criar nome novo com abertura exclusiva (`x` ou `O_EXCL`); retorno é só o caminho local. `run_evaluation` retorna 3 se a captura falhar, 1 quando qualquer caso não passar, 0 somente para todos `PASS`.

- [x] **Step 1: Escrever testes RED do módulo privado.** Sob raiz sintética `<tmp>/outputs/repo` controlada por monkeypatch, verificar caminho em `<tmp>/work/private-review` (fora de `outputs`), segunda gravação com mesmo nome sem sobrescrita, JSON inválido sem arquivo, campos públicos ausentes e recusa de `work/` redirecionado para fora. Cobrir também o fallback em clone comum com `git check-ignore` real: aceitar ignorado e recusar não ignorado. Nenhum teste usa o banco anexo.

```python
first = write_private_review({"format_version": 1, "cases": []})
assert first.parent == tmp_path / "work" / "private-review"
with pytest.raises(FileExistsError):
    write_private_review({"format_version": 1, "cases": []})  # nome fixado no teste
assert json.loads(first.read_text(encoding="utf-8"))["cases"] == []
```

- [x] **Step 2: Escrever testes RED de integração.** Usar `_ScriptedModel` existente com banco sintético Q2: chamada padrão não cria pacote; opção privada grava um registro com texto, SQL selecionado, SQLs bem-sucedidos e tabela somente com agregado igual; erro roteirizado cria registro categórico, campos de resposta nulos e `completed_cases=0`; stdout nunca contém resposta, SQL literal, chave ou mensagem de exceção. `main(["--private-review", "2"])` com preflight inválido retorna 3 sem arquivo. Cobrir também oráculo vazio antes do provedor e diferença entre ausência de resultado (`null`) e resultado vazio (`0`).
- [x] **Step 3: Rodar RED.** Run: `.venv\Scripts\python.exe -m pytest tests/test_private_review.py tests/test_live_eval.py -q --basetemp ..\..\work\pytest-review-red-20261022 --tb=short`. Foram observadas falhas das interfaces novas, de fallback sem ignore, oráculo vazio e contagem ausente antes das respectivas correções; os testes existentes permaneceram preservados.
- [x] **Step 4: Implementar escritor privado.** `json.dumps(..., ensure_ascii=False, allow_nan=False)` antes de `os.open(..., os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)`; `work/` e subdiretório devem resolver dentro da raiz fixa da tarefa ou, no fallback, do repo. Metadados Git usam `subprocess.run` com argumentos estáticos e stderr capturado; não guardar `git diff` nem caminhos. Hash do banco usa leitura em blocos. Erros não incluem texto bruto em stdout.

```python
serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(serialized)
    handle.flush()
    os.fsync(handle.fileno())
```

- [x] **Step 5: Integrar captura no fim de `run_evaluation`.** Pré-validar pasta/Git/hash/árvore limpa e oráculos selecionados não vazios antes do loop quando `private_review=True`; construir os registros por caso após `assess_case` ou erro tratado, sem `str(exc)`; criar pacote com `selected_cases`, `attempted_cases`, `completed_cases`, `error_code`, `integrity_issues`, `database_sha256_before/after` e estado Git antes/depois. Rechecar fonte e banco no fim; integridade inválida devolve código 3 sem apagar o pacote. `main` adiciona apenas flag booleana `--private-review`, sem argumento de caminho. `answer.data.rows` só entra se `outcome.aggregate_match`; capturar `KeyboardInterrupt` como `execution_KeyboardInterrupt` quando possível.

```python
case_record = {
    "case": case,
    "status": outcome.status,
    "issues": list(outcome.issues),
    "answer_text": answer.text if answer is not None else None,
    "selected_sql": selected_sql if answer is not None else None,
    "result_rows": answer.data.rows if answer is not None and outcome.aggregate_match else None,
}
```

- [x] **Step 6: Rodar GREEN, suite completa e revisão do diff.** A árvore compartilhada passou com 372 testes, 1 skip de symlink no Windows, Ruff check/format, `pip check` e `git diff --check`. A revisão direta e independente não encontrou serialização de `steps.error`, `QueryResult` intermediário, exceção ou objeto do provedor; resta scan de segredos e commit antes da rodada real.
- [ ] **Step 7: Commit local antes da rodada real.** Run: `git add README.md docs/superpowers/specs/2026-10-22-fechamento-evidencias-design.md docs/superpowers/plans/2026-10-22-fechamento-evidencias.md evals/live_eval.py evals/private_review.py tests/test_live_eval.py tests/test_private_review.py` e `git commit -m "feat: capture private evaluation evidence"`; depois `git status --short` deve estar vazio.

### Task 3: Uma rodada real e revisão humana privada

**Files:** Create only `<task>/work/private-review/<id>.json` via CLI and `<task>/work/private-review/<id>-review.md` via revisão neste workspace (ou `<repo>/work/` ignorado no fallback); do not modify public code during the run.

**Interfaces:** `DATABASE_PATH` aponta ao anexo local e `OPENAI_API_KEY` permanece em variável privada. A CLI `python -m evals.live_eval --private-review` executa Q1–Q5 uma vez; o pacote identifica commit limpo e hash do banco antes/depois. A matriz aponta ao hash SHA-256 do JSON e não o altera.

- [ ] **Step 1: Preflight sem expor valores.** Verificar presença do banco e da variável, `git status --short` vazio, destino resolvido em `<task>/work` fora de `outputs` (ou `git check-ignore work/private-review` no fallback), conteúdo rastreado via `git ls-files`, e hash do banco lido antes da rodada. Não imprimir valor da chave, dados por linha nem SQL literal.
- [ ] **Step 2: Executar exatamente uma rodada das cinco perguntas.** Com `DATABASE_PATH` e `OPENAI_API_KEY` já configuradas no processo local, run: `.venv\Scripts\python.exe -m evals.live_eval --private-review`. Guardar somente o resumo redigido e exit code; falha/custo interrompem novas chamadas até diagnóstico.
- [ ] **Step 3: Verificar o pacote e construir matriz privada.** Conferir JSON válido, `selected_cases=[1,2,3,4,5]`, casos tentados/concluídos, status e hash do banco igual antes/depois; revisar Q1–Q5 contra a checklist da spec. Escrever `CONFIRMADA`/`INCORRETA`/`INDETERMINADA` por caso, justificativa sem copiar respostas/SQL, nome e SHA-256 do pacote. Preservar pacote e matriz ignorados, sem sobrescrita.
- [ ] **Step 4: Se surgir defeito novo, reproduzir offline antes de corrigir.** Criar fixture sintética que falha pelo mesmo motivo; rodar RED, corrigir mínimo, GREEN, commit e uma decisão explícita se uma **nova** rodada paga é necessária. Não selecionar a melhor de várias amostras.

### Task 4: Fechamento público e gates de entrega

**Files:** Modify `README.md` e `docs/STATUS-ENTREGA.md` somente com afirmações comprovadas; criar novo ZIP em `outputs` apenas se o pacote anterior precisar atualização.

- [ ] **Step 1: Registrar resultado público honesto.** Número de `PASS`/`REVIEW`/`FAIL` da nova amostra, conclusão humana por caso sem texto ou SQL literal, limitações, execução local e aviso de dados privados. Não inferir aprovação histórica nem garantia estatística.
- [ ] **Step 2: Repetir gates finais.** Run: pytest completo com `--basetemp` novo, Ruff, `pip check`, `git diff --check`, scan de segredos no diff e arquivos novos, Playwright CLI contra app local de teste. Comparar `git ls-files` e manifesto do ZIP; nenhum `work/`, `.db`, `.env`, chave ou pacote privado.
- [ ] **Step 3: Revisar e versionar apenas arquivos publicáveis.** Inspecionar `git diff` integral, status e commit; integrar a branch local com `main` somente depois de gates verdes e então atualizar o GitHub previamente autorizado. Se houver gate vermelho ou dado privado detectado, não publicar.
- [ ] **Step 4: Handoff.** Informar link público, spec/plano, testes, estado da avaliação e matriz privada sem expor seu conteúdo; explicitar que o envio à Franq ainda depende do usuário.

## Skills e limites por etapa

`using-superpowers` e `task-observer` atravessam a etapa. `brainstorming`, `escopo-fechado`, `codebase-design`, `writing-plans` e `doc-coauthoring` sustentam a spec; `task-impl`, `test-driven-development` e `systematic-debugging` entram na implementação/regressão; `secret-scanning`, `code-reviewer` e `verification-before-completion` fecham a revisão; `playwright-cli` confirma o fluxo web. `openai-docs` só é necessário se a integração de API mudar; este plano preserva a chamada existente. Strix foi dispensado pelo usuário neste case.
