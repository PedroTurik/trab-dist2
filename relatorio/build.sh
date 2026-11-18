#!/usr/bin/env bash
# Monta relatorio_final.md (insere as tabelas geradas por lab/analise/analise.py) e gera relatorio.pdf
# com pandoc + LaTeX em conteiner (imagem pandoc/latex), sem instalar nada no host.
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'EOF'
import re
s = open("RELATORIO.md").read() + "\n\n" + open("parte2.md").read() + "\n\n" + open("referencias.md").read()
s = re.sub(r"\{\{tabela:([\w.]+)\}\}", lambda m: open("tabelas/" + m.group(1)).read().strip(), s)
open("relatorio_final.md", "w").write(s)
EOF
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/data" -v /usr/share/fonts:/usr/share/fonts/host:ro pandoc/latex:3.5 relatorio_final.md -o relatorio.pdf \
  --pdf-engine=xelatex -V geometry:margin=2cm -V fontsize=10pt -V mainfont="Liberation Serif" -V sansfont="Liberation Sans" \
  -V monofont="Liberation Mono" -V colorlinks=true --toc -V lang=pt-BR -V table-use-row-colors=false
echo "gerado: $PWD/relatorio.pdf"
