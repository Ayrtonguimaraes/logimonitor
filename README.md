# LogiMonitor Bebidas

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

O banco `dados.sqlite3` é criado automaticamente com 60 entregas fictícias, com datas relativas ao primeiro uso. A maioria segue a rota dentro do previsto; seis casos-problema (`DEMO_CASES` em `app.py`) cobrem os cenários típicos: duas entregas retidas no Hub João Pessoa (uma com avaria), uma transferência lenta entre Campina Grande e Mossoró com previsão de atraso, uma tentativa de entrega sem sucesso em Natal e duas entregas concluídas com atraso. Os dados permanecem salvos após reiniciar. Para recomeçar, use **Restaurar dados de demonstração** no rodapé (apaga os registros atuais) ou remova o arquivo com o servidor encerrado.

## Demonstração

1. Abra o **Painel de decisão**: a faixa informa se a operação está dentro das metas e o **Assistente de insights** resume a situação e lista as ações em ordem de urgência. Filtre uma frota e veja a análise mudar.
2. Clique em um indicador (por exemplo, *Entregas em atraso*) para ver quais entregas o compõem.
3. Compare as frotas no **ranking** e clique no nome de uma delas para filtrar.
4. Abra a **Central de alertas**, clique no bloco **Atrasadas** e use **Copiar cobrança** de uma frota (mensagem única com todas as pendências dela).
5. Abra uma entrega atrasada (ex.: PED0012): veja o trajeto, o trecho de maior atraso e a previsão de chegada. Registre a próxima movimentação (o formulário já vem com a próxima parada da rota) e veja os indicadores serem recalculados.
6. Cadastre uma entrega nova (o código é sugerido), registre eventos e exporte o CSV.
7. Consulte **Conceitos e regras** para justificar metas, semáforo e alertas. Use *Restaurar dados de demonstração* (rodapé) para recomeçar.

## Apoio à decisão

- **Metas e semáforo:** cada KPI é verde, amarelo ou vermelho conforme as metas (`TARGETS` em `app.py`): OTD ≥ 95% (amarelo ≥ 85%), ocorrências ≤ 5% (amarelo ≤ 10%), atrasos 0 (amarelo ≤ 3) e tempo médio comparado ao prazo prometido. Toda cor vem com texto.
- **Alertas com severidade:** *Entrega atrasada* (alto; crítico se atrasada há mais de 24 h), *Risco de atraso* (prazo em até 12 h e ainda não saiu para entrega), *Sem atualização* (mais de 24 h) e *Ocorrência* (alto; crítico em caso de falta na carga). Entregas são ordenadas por prioridade.
- **Diagnóstico por cenário:** toda entrega com problema mostra o cenário (saída do CD, transferência entre hubs, retenção no hub, última milha, avaria, tentativa sem sucesso, falta na carga ou entregue com atraso), **onde está o atraso**, o impacto no prazo, os passos recomendados e quem acionar. Aparece no detalhe da entrega e na Central de alertas.
- **Trajeto da entrega:** ao abrir uma entrega, cada parada da rota aparece com o tempo real de cada trecho comparado ao previsto, o ponto de **maior atraso** destacado, onde a carga está agora e a **previsão de chegada** comparada ao prazo. As rotas planejadas (fictícias) ficam em `ROUTES`, no `app.py`.
- **Entregas ao vivo:** diagrama das rotas (estilo mapa de metrô) no painel principal. Cada destino é uma linha com os hubs espaçados pelas horas previstas; cada entrega em andamento aparece na posição estimada pelo último registro e pelo tempo do trecho, colorida por status. Entregas paradas num hub viram um balão com a contagem. Clique para abrir o trajeto.
- **Onde as entregas atrasam:** ranking dos pontos do trajeto que mais acumulam horas além do previsto, somando todas as entregas.
- **Métricas explicadas:** cada indicador tem uma frase simples (ex.: “≈ 9 de cada 10 chegam no prazo”); ao clicar, mostra a conta com os números reais, um pictograma, onde o tempo passa por etapa da rota e onde estão as entregas atrasadas.
- **Ocorrência tratada:** novo evento que encerra o alerta de ocorrência sem apagar o histórico; a taxa de ocorrências continua contando a entrega.
- **Ranking de frotas** por OTD, com tempo médio, atrasos, risco e ocorrências.
- **Assistente de insights** com resumo e recomendações (ver abaixo), tendência de OTD por dia e ocorrências por tipo.
- **Mensagem pronta** para cobrar a frota, atualização automática a cada 60 s e contador de alertas na aba.

## Assistente de insights

O painel principal traz o **Assistente de insights** (`assistant.py`): até 6 recomendações exibidas uma por vez, alternando a cada 8 segundos (com setas e pausa), cada uma com análise, ação sugerida e atalho para as entregas envolvidas. **Não é IA:** cada insight é uma regra analítica explícita aplicada às entregas do filtro, e a análise muda quando os dados ou o filtro mudam.

| Regra | O que verifica | Decisão que apoia |
|---|---|---|
| Atrasos por destino | 50% ou mais dos atrasos em um destino | Separar problema de rota de problema de frota |
| Atrasos por frota | Fatia de atrasos 15 pontos acima da fatia de volume | Cobrar nível de serviço do fornecedor |
| Ocorrências travando entregas | Ocorrências sem tratativa e quantas já atrasaram | Priorizar tratativas |
| Risco de atraso | Prazo próximo e entrega ainda não saiu | Agir antes do atraso |
| Rastreabilidade | Entregas sem atualização, por frota | Cobrar registro de movimentação |
| Simulação de frota | OTD geral se a pior tivesse a pontualidade da melhor | Avaliar redistribuição de volume |
| Tendência | OTD dos últimos 3 dias contra os anteriores | Detectar piora recente |
| Prazo prometido | Tempo médio contra prazo médio prometido | Rever prazo ou tratar exceções |
| Falta na carga | Falta na carga sem tratativa | Acionar frota e seguro |
| Gargalo do trajeto | Ponto da rota com mais horas além do previsto | Saber onde agir na operação |
| Previsão de atraso | Entrega ainda no prazo com chegada prevista depois dele | Agir antes do atraso |

## API

| Rota | Descrição |
|---|---|
| `GET /api/data?carrier=&start=&end=` | Entregas, KPIs, ranking, análise do assistente, tendência e metas, já filtrados pelo servidor. |
| `POST /api/deliveries` | Cadastra entrega. |
| `POST /api/events` | Registra evento (inclui `Ocorrência tratada`). |
| `POST /api/reset` | Restaura os dados de demonstração (apaga os registros atuais). |

As requisições `POST` exigem `Content-Type: application/json` e `Origin`/`Host` locais.

## Estrutura e processamento

- `app.py`: servidor HTTP local, validação, banco SQLite, regras logísticas, KPIs, alertas e recomendações (única fonte dos cálculos).
- `static/index.html`, `static/app.css`, `static/app.js`: interface (apenas exibe os resultados calculados pelo servidor), histórico e CSV.
- `assistant.py`: assistente de insights (regras analíticas que geram o resumo e as recomendações do painel).
- `test_app.py`: testes das regras de negócio, do assistente de insights e da API HTTP com banco temporário.
- `dados.sqlite3`: entregas e eventos persistidos; gerado ao executar.

Entrada: dados da entrega e eventos informados pelo usuário.
Processamento: validação cronológica, gravação em transação, histórico ordenado e cálculo de KPIs e alertas.
Saída: painel, histórico, lista de alertas e arquivo CSV.

OTD = concluídas no prazo / concluídas × 100. Tempo médio = média das horas corridas entre coleta e conclusão. Atrasadas = pendentes com prazo vencido. Taxa de ocorrências = entregas com ao menos uma ocorrência / total × 100. Sem denominador, exibe “—”.

Período filtra pela coleta. Eventos usam horário local, sem conversão de fuso. Os alertas são recalculados a cada atualização (automática a cada 60 s ou manual); não há notificações em segundo plano. Ocorrências permanecem sinalizadas até serem tratadas ou até a entrega ser concluída. Dados fictícios, sem GPS, contas ou integrações. Uso local por uma pessoa; não exponha o servidor à internet.

## Testar

Com o ambiente ativado: `python -m unittest -v test_app.py`.

Sem ativar (macOS/Linux): `.venv/bin/python -m unittest -v test_app.py`.
Windows: `.venv\Scripts\python.exe -m unittest -v test_app.py`.

Para apresentar sem internet, basta manter Python instalado e todos os arquivos da pasta disponíveis.
