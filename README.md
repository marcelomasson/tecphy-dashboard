# Painel Comercial Tecphy

Publicado em: https://marcelomasson.github.io/tecphy-dashboard/

## Como atualizar os dados

Extrair do Probus (Gerenciador de Relatórios BI), período desde 01/01/2025, em PDF:

| Relatório | Uso no painel |
|---|---|
| `RESULTADO_CLIENTE` | Base de vendas (custo, ICMS, PIS/COFINS, comissão, frete, resultado por linha) |
| `RESULTADO_VENDEDOR` | Vendedor de cada linha |
| `ENGENHARIAS_PRODUZIDAS` | Receitas (BOM) dos produzidos |
| `RESULTADO_PRODUTO` | Conferência |
| `VENDAS_CLIENTE_DETALHADO` | Conferência (NFs emitidas ainda fora do Resultado) |
| `VENDAS_POR_VENDEDOR` | Conferência |
| `POSICAO_ESTOQUE` (opcional) | Se não vier, mantém a última posição de estoque |

Depois:

```bash
python3 scripts/atualizar_dados.py <pasta_com_os_pdfs> index.html
```

O script confere cada cliente e o Total Geral impresso; se algum valor não bater, ele **aborta sem alterar o painel**.
Trata registros cortados por quebra de página do PDF (valor deslocado é reconstruído pela identidade
resultado = venda − custo − ICMS − PIS/COFINS − comissão − frete) e atribui devoluções sem vendedor ao vendedor da venda original.
