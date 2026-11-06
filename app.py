"""LogiMonitor: servidor local, regras logísticas e persistência SQLite."""
import argparse
import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'dados.sqlite3'
EVENTS = ['Coletado', 'Em trânsito', 'Saiu para entrega', 'Entregue', 'Avaria', 'Extravio', 'Tentativa sem sucesso']
INCIDENTS = EVENTS[4:]
def now():
    return datetime.now().replace(microsecond=0)
def stamp(value):
    return value.isoformat(timespec='seconds')
def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def initialize():
    with connect() as con:
        con.executescript('''CREATE TABLE IF NOT EXISTS deliveries (
        id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, client TEXT NOT NULL,
        origin TEXT NOT NULL, destination TEXT NOT NULL, carrier TEXT NOT NULL,
        collected TEXT NOT NULL, deadline TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,
        delivery_id INTEGER NOT NULL REFERENCES deliveries(id), time TEXT NOT NULL,
        kind TEXT NOT NULL, location TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');''')
        if con.execute('SELECT count(*) FROM deliveries').fetchone()[0]: return
        base = now()
        for i in range(1, 41):
            collected = base - timedelta(days=12-i%11, hours=i%5)
            deadline = collected + timedelta(days=3)
            cur = con.execute('INSERT INTO deliveries(code,client,origin,destination,carrier,collected,deadline) VALUES(?,?,?,?,?,?,?)',
                (f'LOG{i:04}', f'Cliente {i:02}', 'Recife/PE', ['João Pessoa/PB','Natal/RN','Fortaleza/CE','Maceió/AL'][i%4], ['Rota Nordeste','Expresso Litoral','Via Cargas'][i%3],stamp(collected),stamp(deadline)))
            did=cur.lastrowid
            con.execute('INSERT INTO events(delivery_id,time,kind,location,note) VALUES(?,?,?,?,?)',(did,stamp(collected),'Coletado','Recife/PE','Carga recebida no centro de distribuição.'))
            if i%4 != 0:
                end = collected + timedelta(days=2 if i%5 else 4)
                if end <= base:
                    con.execute('INSERT INTO events(delivery_id,time,kind,location,note) VALUES(?,?,?,?,?)',(did,stamp(end),'Entregue','Destino','Recebimento confirmado.'))
            elif i%8==0:
                con.execute('INSERT INTO events(delivery_id,time,kind,location,note) VALUES(?,?,?,?,?)',(did,stamp(collected+timedelta(hours=12)),'Avaria','Centro de distribuição','Embalagem danificada; aguardando tratativa.'))

def snapshot(carrier='', start='', end=''):
    with connect() as con:
        rows=[dict(r) for r in con.execute('SELECT * FROM deliveries ORDER BY collected DESC')]
        events=[dict(r) for r in con.execute('SELECT * FROM events ORDER BY time,id')]
    current=now(); result=[]
    for row in rows:
        if carrier and row['carrier'] != carrier: continue
        if start and row['collected'][:10]<start: continue
        if end and row['collected'][:10]>end: continue
        history=[e for e in events if e['delivery_id']==row['id']]
        delivered=next((e['time'] for e in history if e['kind']=='Entregue'),None)
        row.update(events=history,status=history[-1]['kind'],delivered=delivered,alerts=[])
        if not delivered:
            if datetime.fromisoformat(row['deadline'])<current: row['alerts'].append('Entrega atrasada')
            if current-datetime.fromisoformat(history[-1]['time'])>timedelta(hours=24): row['alerts'].append('Sem atualização há mais de 24h')
            if any(e['kind'] in INCIDENTS for e in history): row['alerts'].append('Ocorrência: verificar histórico')
        result.append(row)
    completed=[r for r in result if r['delivered']]
    punctual=sum(r['delivered']<=r['deadline'] for r in completed)
    duration=sum((datetime.fromisoformat(r['delivered'])-datetime.fromisoformat(r['collected'])).total_seconds()/3600 for r in completed)
    return {'deliveries':result,'kpis':{'total':len(result),'completed':len(completed),'otd':round(100*punctual/len(completed),1) if completed else None,'average_hours':round(duration/len(completed),1) if completed else None,'late':sum('Entrega atrasada' in r['alerts'] for r in result),'incidents':round(100*sum(any(e['kind'] in INCIDENTS for e in r['events']) for r in result)/len(result),1) if result else None},'updated':stamp(current)}

def required(data, key):
    value=str(data.get(key,'')).strip()
    if not value: raise ValueError('Preencha o campo: '+key)
    if len(value)>300: raise ValueError('Campo muito longo: '+key)
    return value

def create_delivery(data):
    values=[required(data,k) for k in ['code','client','origin','destination','carrier','collected','deadline']]
    collected,deadline=map(datetime.fromisoformat, values[-2:])
    if collected>now(): raise ValueError('A coleta não pode estar no futuro.')
    if deadline<collected: raise ValueError('O prazo deve ser igual ou posterior à coleta.')
    values[-2:]=[stamp(collected),stamp(deadline)]
    with connect() as con:
        cur=con.execute('INSERT INTO deliveries(code,client,origin,destination,carrier,collected,deadline) VALUES(?,?,?,?,?,?,?)',values)
        con.execute('INSERT INTO events(delivery_id,time,kind,location,note) VALUES(?,?,?,?,?)',(cur.lastrowid,values[-2],'Coletado',values[2],'Cadastro da entrega.'))

def create_event(data):
    did=int(data['delivery_id']); kind=required(data,'kind'); when=datetime.fromisoformat(required(data,'time'))
    if kind not in EVENTS: raise ValueError('Tipo de evento inválido.')
    location=required(data,'location'); note=str(data.get('note','')).strip()[:1000]
    with connect() as con:
        delivery=con.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone()
        if not delivery: raise ValueError('Entrega não encontrada.')
        last=con.execute('SELECT * FROM events WHERE delivery_id=? ORDER BY time DESC,id DESC LIMIT 1',(did,)).fetchone()
        if last['kind']=='Entregue': raise ValueError('A entrega já foi concluída.')
        if when<datetime.fromisoformat(last['time']): raise ValueError('O evento deve ser posterior ou igual ao último registro.')
        if when>now(): raise ValueError('O evento não pode estar no futuro.')
        con.execute('INSERT INTO events(delivery_id,time,kind,location,note) VALUES(?,?,?,?,?)',(did,stamp(when),kind,location,note))

class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime='application/json; charset=utf-8'):
        if isinstance(body,(dict,list)): body=json.dumps(body,ensure_ascii=False).encode()
        if isinstance(body,str): body=body.encode()
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/data': return self.send(200,snapshot())
        if path=='/': return self.send(200,(ROOT/'static/index.html').read_bytes(),'text/html; charset=utf-8')
        self.send(404,{'error':'Página não encontrada.'})
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>20000: raise ValueError('Requisição muito grande.')
            data=json.loads(self.rfile.read(length))
            if self.path=='/api/deliveries': create_delivery(data)
            elif self.path=='/api/events': create_event(data)
            else: return self.send(404,{'error':'Rota não encontrada.'})
            self.send(201,{'ok':True})
        except sqlite3.IntegrityError: self.send(400,{'error':'Código de rastreio já cadastrado ou referência inválida.'})
        except (ValueError,KeyError,TypeError) as exc: self.send(400,{'error':str(exc)})

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8501); args=parser.parse_args()
    initialize(); print(f'LogiMonitor disponível em http://localhost:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
