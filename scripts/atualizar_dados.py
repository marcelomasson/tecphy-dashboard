#!/usr/bin/env python3
"""
Atualiza os dados do Painel Tecphy a partir dos relatórios PDF do Probus (Wonder Sistemas).

Uso:
    python3 scripts/atualizar_dados.py <pasta_com_os_pdfs> [index.html]

Relatórios esperados na pasta (o nome só precisa CONTER estas palavras):
    RESULTADO_CLIENTE        -> base de vendas (custo, impostos, comissão, frete, resultado por linha)
    RESULTADO_VENDEDOR       -> vendedor de cada linha
    ENGENHARIAS_PRODUZIDAS   -> receitas (BOM) dos produzidos
    RESULTADO_PRODUTO        -> conferência
    VENDAS_CLIENTE_DETALHADO -> conferência (vendas emitidas que ainda não estão no Resultado)
    VENDAS_POR_VENDEDOR      -> conferência
    POSICAO_ESTOQUE          -> (opcional) se não vier, mantém o estoque atual do painel

O script:
  1. lê os PDFs (pdftotext -layout), tolerando quebras de página no meio de um registro;
  2. reconcilia linha a linha com os "Total" impressos de cada cliente e o "Total Geral";
  3. aborta SEM alterar o painel se algum total não bater;
  4. grava os dados no index.html e imprime um resumo.
"""
import sys, os, re, json, glob, subprocess, unicodedata
from collections import defaultdict

NUM = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d+%?$')
SKIP = re.compile(r'(TECPHY AGROINDUSTRIAL LTDA - Tijucas|Resultado de Vendas por|^\s*Período|Wonder Sistemas|BI Report Id|^\s*Data\s+Emp|Página \d+|Lista de Produzidos|Código Material|Vendas por (Vendedor|Cliente)|^\s*Data\s+Documento)')

def br(x):
    x = x.strip().rstrip('%')
    neg = x.startswith('-')
    v = float(x.lstrip('-').replace('.', '').replace(',', '.'))
    return -v if neg else v

def find(folder, key):
    fs = [f for f in glob.glob(os.path.join(folder, '*.pdf')) if key in os.path.basename(f).upper()]
    return sorted(fs)[-1] if fs else None

def text_lines(pdf):
    out = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True, check=True).stdout
    return out.replace('\f', '\n').split('\n')

def split_numeric_tail(tokens, n):
    """Encontra a última janela de n tokens numéricos consecutivos. Retorna (antes, nums, depois)."""
    for end in range(len(tokens), n - 1, -1):
        win = tokens[end - n:end]
        if all(NUM.match(t) for t in win):
            return tokens[:end - n], [br(t) for t in win], tokens[end:]
    return None

def tipo_e_nome(desc):
    d = desc.strip()
    m = re.match(r'^\[(REVENDA|P|PRODUZIDO)\]\s*(.*)$', d)
    if m:
        return ('Revenda' if m.group(1) == 'REVENDA' else 'Produzido'), m.group(2).strip()
    return 'Outro', d

# ---------------------------------------------------------------- RESULTADO POR CLIENTE
def parse_resultado_cliente(pdf):
    lines = text_lines(pdf)
    rec_re = re.compile(r'^\s*(\d{2}/\d{2}/\d{4})\s+(\d+)\s+(\S+)(?:\s+(\S+))?(.*)$')
    rows, totals, grand = [], {}, None
    expect_client = True
    fix_log = []
    cli_cod = cli_nome = None
    cur = None

    def close():
        nonlocal cur
        if not cur: return
        toks = ' '.join(cur['txt']).split()
        # localiza a sequência de valores que termina no "% Resultado"
        pi = max((i for i, t in enumerate(toks) if t.endswith('%') and NUM.match(t)), default=None)
        if pi is None:
            raise ValueError(f"Registro sem valores: {cur['doc']} {cur['cod']} {' '.join(toks)[:120]}")
        st = pi
        while st > 0 and NUM.match(toks[st-1]) and not toks[st-1].endswith('%'): st -= 1
        run = [br(t) for t in toks[st:pi+1]]
        stray = [t for t in toks[pi+1:] if NUM.match(t)]
        before = toks[:st]; after = [t for t in toks[pi+1:] if not NUM.match(t)]
        if len(run) > 12: before += toks[st:st+len(run)-12]; run = run[-12:]
        if len(run) == 11:   # um valor caiu para outra página: reconstrói pela identidade do relatório
            q, vu, v, dsc, cu, ct, ic, pc, cm, res, pr = run
            fr = round(v - ct - ic - pc - cm - res, 2)
            run = [q, vu, v, dsc, cu, ct, ic, pc, cm, fr, res, pr]
            fix_log.append((cur['doc'], cur['cod'], fr, stray))
        if len(run) != 12:
            raise ValueError(f"Registro com {len(run)} valores: {cur['doc']} {cur['cod']} {' '.join(toks)[:160]}")
        n = run
        unid = before[-1] if before else ''
        desc = ' '.join(before[:-1] + after)
        tipo, nome = tipo_e_nome(desc)
        d = cur['data']
        rows.append(dict(data=d, ano=int(d[6:]), anoMes=d[6:] + '-' + d[3:5], clienteCod=cli_cod, cliente=cli_nome,
                         documento=cur['doc'], produtoCod=cur['cod'], produto=nome, tipo=tipo, unid=unid,
                         qtd=n[0], vendaUnit=n[1], venda=n[2], custoUnit=n[4], custoTotal=n[5], icms=n[6],
                         pis=n[7], comissao=n[8], frete=n[9], resultado=n[10]))
        cur = None

    for ln in lines:
        if not ln.strip() or SKIP.search(ln):
            continue
        m = re.match(r'^\s*Cliente:\s*(\d+)-(.*)$', ln) or (expect_client and re.match(r'^\s*(\d+)-(\D.*)$', ln))
        if m:
            close(); cli_cod, cli_nome = m.group(1), m.group(2).strip(); expect_client = False; continue
        if re.match(r'^\s*Cliente:\s*$', ln):
            close(); expect_client = True; continue
        if re.search(r'Total Geral:', ln):
            close()
            toks = ln.split('Total Geral:')[1].replace(':', ' ').split()
            nums = [br(t) for t in toks if NUM.match(t)]
            grand = dict(qtd=nums[0], venda=nums[1], custo=nums[3], resultado=nums[-2]); continue
        if re.match(r'^\s*Total\s', ln):
            close()
            nums = [br(t) for t in ln.split(':')[-1].split() if NUM.match(t)]
            tcod = re.search(r'Total\s+(\d+)-', ln)
            totals[tcod.group(1) if tcod else cli_cod] = dict(venda=nums[1], resultado=nums[-2]); expect_client = True; continue
        m = rec_re.match(ln)
        if m:
            close()
            cur = dict(data=m.group(1), doc=m.group(3), cod=m.group(4), txt=[m.group(5) or '']); continue
        if cur:
            if not cur['cod']:
                parts = ln.split(None, 1); cur['cod'] = parts[0]; ln = parts[1] if len(parts) > 1 else ''
            cur['txt'].append(ln)
    close()
    if fix_log: print(f'  {len(fix_log)} linha(s) com valor deslocado por quebra de página — reconstruídas: ' + ', '.join(f'{d} {c} frete={f:.2f}' for d,c,f,_ in fix_log))
    return rows, totals, grand

# ---------------------------------------------------------------- RESULTADO POR VENDEDOR
def parse_resultado_vendedor(pdf):
    lines = text_lines(pdf)
    rec_re = re.compile(r'^\s*(\d{2}/\d{2}/\d{2})\s+(\d+)\s+(.*)$')
    out, vend, cur, grand = [], None, None, None

    def close():
        nonlocal cur
        if not cur: return
        toks = ' '.join(cur['txt']).split()
        di = next((i for i, t in enumerate(toks) if re.match(r'^[A-Z]{2,4}-\d+$', t)), None)
        if di is not None:
            j = di + 1
            while j < len(toks) and re.match(r'^\d{2}/\d{2}/\d{2}$', toks[j]): j += 1
            rest = toks[j+1:]
            idx = [i for i, t in enumerate(rest) if NUM.match(t)]
            if j < len(toks) and len(idx) >= 3:
                out.append(dict(doc=toks[di], cod=toks[j], qtd=br(rest[idx[0]]), venda=br(rest[idx[2]]), vendedor=vend))
        cur = None

    for ln in lines:
        if not ln.strip() or SKIP.search(ln):
            continue
        m = re.match(r'^\s*Vendedor:\s*\d+\s*-\s*(.*)$', ln)
        if m:
            close(); vend = m.group(1).strip(); continue
        if 'Total Geral:' in ln:
            close(); nums = [br(t) for t in ln.split('Total Geral:')[1].split() if NUM.match(t)]
            grand = dict(venda=nums[1]); continue
        if re.match(r'^\s*Total\s', ln):
            close(); continue
        m = rec_re.match(ln)
        if m:
            if cur:
                jt = ' '.join(cur['txt']).split()
                incompleto = any(re.match(r'^[A-Z]{2,4}-\d+$', t) for t in jt) and sum(bool(NUM.match(t)) for t in jt) < 3
                if incompleto:   # registro cortado pela quebra de página: o restante vem nesta linha
                    cur['txt'].append(m.group(3)); continue
            close(); cur = dict(txt=[m.group(3)]); continue
        if re.match(r'^\s*[A-Z]{2,4}-\d+\s+\d{2}/\d{2}/\d{2}', ln) and not (cur and not any(re.search(r'[A-Z]{2,4}-\d+', t) for t in cur['txt'])):
            close(); cur = dict(txt=[ln]); continue
        if cur:
            cur['txt'].append(ln)
    close()
    return out, grand

# ---------------------------------------------------------------- VENDAS (conferência)
def parse_vendas_detalhado(pdf):
    docs = defaultdict(float)
    for ln in text_lines(pdf):
        m = re.match(r'^\s*(\d{2}/\d{2}/\d{4})\s+(NFE-\S+|\S+-\d+)\s+(\S+)\s+(.*)$', ln)
        if m:
            nums = [t for t in m.group(4).split() if NUM.match(t)]
            if nums:
                docs[(m.group(2), m.group(1))] += br(nums[-1])
    return docs

# ---------------------------------------------------------------- ENGENHARIAS
def parse_engenharias(pdf):
    prods, mats, cur = [], [], None
    for ln in text_lines(pdf):
        if not ln.strip() or SKIP.search(ln):
            continue
        m = re.match(r'^\s*Produto:\s*(\S+?)-(.*?)\s+(?:(KG|UN|L|FRA|PC|G)\s+)?Qtd Estoque:\s*(\S+)\s+Custo Total:\s*(\S+)', ln)
        if m:
            cur = m.group(1)
            prods.append([cur, m.group(2).strip(), m.group(3) or 'KG', br(m.group(4)), br(m.group(5))]); continue
        if not cur:
            continue
        m = re.match(r'^(\S+)\s{2,}(?![\s|])(.*?)\s{2,}(KG|UN|L|FRA|PC|G)\s+(\S+)\s+(\S+)\s+(\S+)\s*$', ln)
        if m and all(NUM.match(x) for x in m.group(4, 5, 6)):
            mats.append([cur, m.group(1), m.group(2).strip(), m.group(3), br(m.group(4)), br(m.group(5)), br(m.group(6))])
    return prods, mats

# ---------------------------------------------------------------- MAIN
def main():
    folder = sys.argv[1]
    html_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), '..', 'index.html')
    f_cli = find(folder, 'RESULTADO_CLIENTE'); f_ven = find(folder, 'RESULTADO_VENDEDOR')
    f_eng = find(folder, 'ENGENHARIA'); f_vcd = find(folder, 'VENDAS_CLIENTE')
    if not f_cli:
        sys.exit('ERRO: não encontrei o relatório RESULTADO_CLIENTE na pasta.')

    rows, totals, grand = parse_resultado_cliente(f_cli)
    # ---- reconciliação por cliente e geral
    by = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        by[r['clienteCod']][0] += r['venda']; by[r['clienteCod']][1] += r['resultado']
    erros = [(c, t['venda'], by[c][0]) for c, t in totals.items() if abs(t['venda'] - by[c][0]) > 0.05 or abs(t['resultado'] - by[c][1]) > 0.05]
    tv = sum(r['venda'] for r in rows); tr = sum(r['resultado'] for r in rows)
    print(f'Resultado por Cliente: {len(rows)} linhas, {len(totals)} clientes')
    print(f'  Faturamento lido R$ {tv:,.2f} | impresso R$ {grand["venda"]:,.2f}')
    print(f'  Resultado   lido R$ {tr:,.2f} | impresso R$ {grand["resultado"]:,.2f}')
    if erros or abs(tv - grand['venda']) > 0.5 or abs(tr - grand['resultado']) > 0.5:
        for e in erros[:20]: print('  DIVERGÊNCIA cliente', e)
        sys.exit('ERRO: os totais não batem — painel NÃO foi alterado.')
    print('  ✓ todos os clientes e o Total Geral conferem')

    # ---- vendedor
    if f_ven:
        vrows, vgrand = parse_resultado_vendedor(f_ven)
        pool = defaultdict(list)
        for v in vrows: pool[(v['doc'], v['cod'])].append(v)
        ok = 0
        for r in rows:
            cands = pool.get((r['documento'], r['produtoCod']), [])
            pick = next((c for c in cands if abs(c['qtd'] - r['qtd']) < 1e-6 and abs(c['venda'] - r['venda']) < 0.01), None) or (cands[0] if cands else None)
            if pick:
                r['vendedor'] = pick['vendedor']; ok += 1
                if len(cands) > 1: cands.remove(pick)
            else:
                r['vendedor'] = 'Não identificado'
                print(f"   sem vendedor: {r['data']} {r['documento']} {r['produtoCod']} {r['cliente'][:40]} R$ {r['venda']:,.2f}")
        # devolução sem vendedor: herda o vendedor da venda original (mesmo cliente + produto)
        for r in rows:
            if r['vendedor'] == 'Não identificado' and r['venda'] < 0:
                orig = [x for x in rows if x['clienteCod'] == r['clienteCod'] and x['produtoCod'] == r['produtoCod'] and x['venda'] > 0 and x['vendedor'] != 'Não identificado']
                if orig:
                    r['vendedor'] = orig[-1]['vendedor']; ok += 1
                    print(f"   devolução {r['documento']} atribuída a {r['vendedor']} (vendedor da venda original)")
        print(f'Resultado por Vendedor: {len(vrows)} linhas (total R$ {vgrand["venda"]:,.2f}) — vendedor atribuído em {ok}/{len(rows)} linhas ({ok/len(rows):.1%})')
    else:
        for r in rows: r['vendedor'] = 'Não identificado'
        print('AVISO: sem RESULTADO_VENDEDOR — vendedor ficará "Não identificado"')

    # ---- conferência: vendas emitidas que ainda não aparecem no Resultado
    pendentes = []
    if f_vcd:
        vend_docs = parse_vendas_detalhado(f_vcd)
        res_docs = {r['documento'] for r in rows}
        pendentes = sorted([(d, dt, v) for (d, dt), v in vend_docs.items() if d not in res_docs and v > 0], key=lambda x: x[1][6:] + x[1][3:5] + x[1][:2])
        if pendentes:
            print(f'Conferência: {len(pendentes)} NF(s) em "Vendas" que ainda não constam no "Resultado" (R$ {sum(p[2] for p in pendentes):,.2f}):')
            for p in pendentes[-10:]: print('   ', p[1], p[0], f'R$ {p[2]:,.2f}')

    # ---- engenharias
    html = open(html_path, encoding='utf-8').read()
    m = re.search(r'const DATA = (\{.*?\});\n', html, re.S)
    old = json.loads(m.group(1))
    if f_eng:
        prods, mats = parse_engenharias(f_eng)
        print(f'Engenharias: {len(prods)} receitas, {len(mats)} componentes de 1º nível')
    else:
        prods, mats = old['engProd'], old['engMat']
        print('AVISO: sem ENGENHARIAS — mantidas as receitas atuais')

    # ---- estoque (opcional)
    estoque = old['estoque']; estoque_data = old['meta'].get('estoqueData', '03/09/2026')
    print(f'Estoque: mantida a posição de {estoque_data} (nenhum relatório de Posição de Estoque nesta rodada)')

    datas = sorted(rows, key=lambda r: (r['data'][6:], r['data'][3:5], r['data'][:2]))
    keys = ["data","ano","anoMes","vendedor","clienteCod","cliente","documento","produtoCod","produto","tipo","unid","qtd","vendaUnit","venda","custoUnit","custoTotal","icms","pis","comissao","frete","resultado"]
    # data de emissão do relatório
    emis = re.search(r'Emissão:\s*(\d{2}/\d{2}/\d{4})', '\n'.join(text_lines(f_cli)))
    gerado = emis.group(1) if emis else datas[-1]['data']
    new = dict(
        meta=dict(periodo=f"{datas[0]['data']} a {datas[-1]['data']}", gerado=gerado, empresa='TECPHY AGROINDUSTRIAL LTDA',
                  estoqueData=estoque_data, pendentes=[[p[1], p[0], round(p[2], 2)] for p in pendentes]),
        salesKeys=keys, sales=[[r[k] for k in keys] for r in rows],
        estoqueKeys=old['estoqueKeys'], estoque=estoque,
        engProdKeys=old['engProdKeys'], engProd=prods, engMatKeys=old['engMatKeys'], engMat=mats)
    html = html[:m.start(1)] + json.dumps(new, ensure_ascii=False, separators=(',', ':')) + html[m.end(1):]
    open(html_path, 'w', encoding='utf-8').write(html)
    print(f'✓ Painel atualizado: {len(rows)} linhas de venda, período {new["meta"]["periodo"]}, dados até {gerado}')

if __name__ == '__main__':
    main()
