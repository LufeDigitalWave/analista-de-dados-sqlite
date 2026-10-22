-- Oráculos independentes para reproduzir os agregados do desafio.
-- Este arquivo NÃO é importado pelo agente nem pelo aplicativo.

-- 1. Cinco estados, desempate alfabético, maio mais recente: 2025.
SELECT c.estado, COUNT(DISTINCT p.cliente_id) AS clientes
FROM compras AS p
JOIN clientes AS c ON c.id = p.cliente_id
WHERE p.canal = 'App'
  AND p.data_compra >= '2025-05-01'
  AND p.data_compra < '2025-06-01'
GROUP BY c.estado
ORDER BY clientes DESC, c.estado ASC
LIMIT 5;

-- 2. Clientes (não interações) de campanhas WhatsApp em 2024.
SELECT COUNT(DISTINCT cliente_id) AS clientes
FROM campanhas_marketing
WHERE canal = 'WhatsApp'
  AND interagiu = 1
  AND data_envio >= '2024-01-01'
  AND data_envio < '2025-01-01';

-- 3. Compras por comprador distinto de cada categoria.
SELECT categoria,
       ROUND(CAST(COUNT(*) AS REAL) / COUNT(DISTINCT cliente_id), 4) AS compras_por_comprador
FROM compras
GROUP BY categoria
ORDER BY compras_por_comprador DESC;

-- 4. Apenas reclamações não resolvidas.
SELECT canal, COUNT(*) AS reclamacoes
FROM suporte
WHERE tipo_contato = 'Reclamação' AND resolvido = 0
GROUP BY canal
ORDER BY reclamacoes DESC;

-- 5. Série mensal por canal, na janela de um ano até a última data de suporte.
WITH limite AS (SELECT MAX(date(data_contato)) AS fim FROM suporte)
SELECT strftime('%Y-%m', s.data_contato) AS mes,
       s.canal,
       COUNT(*) AS reclamacoes
FROM suporte AS s
CROSS JOIN limite AS l
WHERE s.tipo_contato = 'Reclamação'
  AND date(s.data_contato) BETWEEN date(l.fim, '-1 year') AND l.fim
GROUP BY mes, s.canal
ORDER BY mes, s.canal;
