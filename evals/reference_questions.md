# Perguntas de referência do banco fornecido

Valores obtidos por consultas agregadas locais em `anexo_desafio_1.db`. O arquivo original permanece fora do repositório. Estes oráculos servem para revisar respostas do agente; não são importados pela aplicação.

As consultas que reproduzem os números estão em [reference_queries.sql](reference_queries.sql). Elas são material de avaliação, não código do agente; sem o banco privado, os números não podem ser recalculados por quem apenas clonar o repositório.

1. **“Liste os 5 estados com maior número de clientes que compraram via App em maio.”** Interpretar como maio de 2025 (o maio mais recente no banco), `COUNT(DISTINCT cliente_id)` após filtrar compras por canal App e data. Com desempate alfabético: São Paulo 6, Minas Gerais 3, Santa Catarina 3, Alagoas 2, Espírito Santo 2. Paraná também tem 2 e pode aparecer em outro desempate igualmente válido.
2. **“Quantos clientes interagiram com campanhas de WhatsApp em 2024?”** Contar clientes distintos com `interagiu=1`, canal WhatsApp e envio em 2024: **17**. Contar interações em vez de pessoas é outra métrica.
3. **“Quais categorias tiveram mais compras em média por cliente?”** Denominador: compradores distintos *da própria categoria*, não todos os clientes da tabela `clientes`. Média em ordem decrescente: Roupas 2,2113; Viagens 2,1622; Livros 1,9759; Serviços 1,9620; Eletrônicos 1,9231; Alimentos 1,8824. Declarar o denominador na resposta.
4. **“Qual o número de reclamações não resolvidas por canal?”** Filtrar `tipo_contato='Reclamação'` e `resolvido=0`: Telefone 19; Chat 18; E-mail 14. `resolvido IS NULL` é estado desconhecido, não “não resolvido” por padrão, e não integra a contagem. A ampliação simples do filtro por `OR resolvido IS NULL` com `COUNT(*)` é semanticamente incorreta mesmo se não mudar os números deste banco; outras formas de agregação precisam de revisão própria. Somar todos os contatos não resolvidos, sem o filtro de reclamação, também estaria incorreto.
5. **“Qual a tendência de reclamações por canal no último ano?”** Usar a maior data de suporte disponível, 22/07/2025, como referência; janela de 22/07/2024 a 22/07/2025, incluindo meses de julho parciais. Agrupar por mês e canal; total **97** reclamações no período: Telefone 35, Chat 34, E-mail 28. Uma visualização de linha deve manter as três séries, não somente o total.

Diferenças de período, desempate ou denominador podem produzir valores alternativos legítimos quando explicitadas. A avaliação deve conferir o SQL e a interpretação, não apenas comparar texto.
