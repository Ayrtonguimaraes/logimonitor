# LogiMonitor

Projeto acadêmico de monitoramento de KPIs, rastreabilidade e alertas logísticos.

## Executar

Requer Python 3.9 ou superior. Sem pacotes adicionais.

Os inicializadores criam automaticamente um ambiente virtual `.venv` exclusivo do projeto e executam o servidor com o Python desse ambiente.

**Mac:** abra `Iniciar.command`. **Windows:** abra `Iniciar.bat`.
Depois, acesse http://localhost:8501 e mantenha o terminal aberto. Para encerrar, pressione Ctrl+C.

### Executar manualmente (macOS/Linux)

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

### Executar manualmente (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Não é necessário ativar o ambiente no PowerShell: os comandos acima usam diretamente seu executável. Se a porta estiver ocupada, acrescente `--port 8502` ao comando de execução.

### Compartilhar pelo GitHub

Cada integrante deve clonar o repositório ou baixar o ZIP em **Code → Download ZIP**, extrair e executar o inicializador do seu sistema. Cada computador cria sua própria `.venv` e seu próprio banco. O ambiente e o banco não são enviados ao GitHub. O GitHub compartilha o código; ele não hospeda este servidor Python nem sincroniza os dados entre integrantes.

O banco `dados.sqlite3` é criado automaticamente com 40 entregas fictícias, com datas relativas ao primeiro uso. Os dados permanecem salvos após reiniciar. Para começar novamente, encerre o servidor, faça uma cópia do banco se precisar preservar os registros e remova o arquivo original; a próxima execução gera uma nova demonstração.

## Demonstração

1. Observe os quatro KPIs no painel e filtre uma transportadora.
2. Abra a central de alertas e consulte o histórico de uma entrega atrasada.
3. Registre um evento de entrega com o horário atual e local de destino.
4. Volte ao painel: o alerta desaparece e os indicadores são recalculados.
5. Cadastre uma entrega, registre um evento e exporte as entregas em CSV.
6. Consulte a aba Conceitos e regras para justificar os cálculos.

## Estrutura e processamento

- `app.py`: servidor HTTP local, validação, banco SQLite e regras logísticas.
- `static/index.html`: interface, filtros, cálculo dos indicadores filtrados, histórico e CSV.
- `test_app.py`: testes das regras de negócio com banco temporário.
- `dados.sqlite3`: entregas e eventos persistidos; gerado ao executar.

Entrada: dados da entrega e eventos informados pelo usuário.
Processamento: validação cronológica, gravação em transação, histórico ordenado e cálculo de KPIs e alertas.
Saída: painel, histórico, lista de alertas e arquivo CSV.

OTD = concluídas no prazo / concluídas × 100. Tempo médio = média das horas corridas entre coleta e conclusão. Atrasadas = pendentes com prazo vencido. Taxa de ocorrências = entregas com ao menos uma ocorrência / total × 100. Sem denominador, exibe “—”.

Período filtra pela coleta. Eventos usam horário local, sem conversão de fuso. Os alertas são recalculados ao atualizar os dados; não são notificações automáticas em segundo plano. Ocorrências permanecem sinalizadas até concluir a entrega. Dados fictícios, sem GPS, contas ou integrações. Uso local por uma pessoa; não exponha o servidor à internet.

## Testar

Com o ambiente ativado: `python -m unittest -v test_app.py`.

Sem ativar (macOS/Linux): `.venv/bin/python -m unittest -v test_app.py`.
Windows: `.venv\Scripts\python.exe -m unittest -v test_app.py`.

Para apresentar sem internet, basta manter Python instalado e todos os arquivos da pasta disponíveis.
