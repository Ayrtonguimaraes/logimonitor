import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import timedelta
import app

class LogisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=app.DB
        app.DB=Path(self.temp.name)/'test.sqlite3'
        app.initialize()
        with app.session() as con:
            con.execute('DELETE FROM returns')
            con.execute('DELETE FROM items')
            con.execute('DELETE FROM events')
            con.execute('DELETE FROM deliveries')
    def tearDown(self):
        app.DB=self.old
        self.temp.cleanup()
    def delivery(self,code='TEST',hours=48):
        collected=app.now()-timedelta(hours=hours)
        app.create_delivery(dict(code=code,client='Teste',origin='Recife',destination='Natal',carrier='Teste',collected=app.stamp(collected),deadline=app.stamp(collected+timedelta(hours=24))))
        return app.snapshot()['deliveries'][-1]['id']
    def custom(self,code,collected_hours_ago,deadline_hours_from_now,carrier='Teste'):
        collected=app.now()-timedelta(hours=collected_hours_ago)
        deadline=app.now()+timedelta(hours=deadline_hours_from_now)
        app.create_delivery(dict(code=code,client='Teste',origin='Recife',destination='Natal',carrier=carrier,collected=app.stamp(collected),deadline=app.stamp(deadline)))
        return next(d['id'] for d in app.snapshot()['deliveries'] if d['code']==code)
    def event(self,did,kind,when):
        app.create_event(dict(delivery_id=did,kind=kind,time=app.stamp(when),location='Natal'))
    def codes(self,code):
        return [a['code'] for d in app.snapshot()['deliveries'] if d['code']==code for a in d['alert_details']]
    def test_pending_and_delivery(self):
        did=self.delivery()
        data=app.snapshot()
        self.assertEqual(data['kpis']['late'],1)
        self.assertIsNone(data['kpis']['otd'])
        self.assertEqual(len(data['deliveries'][0]['alerts']),2)
        self.event(did,'Entregue',app.now()-timedelta(hours=1))
        data=app.snapshot()
        self.assertEqual(data['kpis']['otd'],0)
        self.assertEqual(data['kpis']['average_hours'],47)
        self.assertEqual(data['deliveries'][0]['alerts'],[])
        with self.assertRaises(ValueError): self.event(did,'Em rota',app.now())
    def test_punctual_and_incidents_count_once(self):
        did=self.delivery()
        self.event(did,'Avaria',app.now()-timedelta(hours=46))
        self.event(did,'Cliente fechado',app.now()-timedelta(hours=45))
        self.event(did,'Entregue',app.now()-timedelta(hours=24))
        self.assertEqual(app.snapshot()['kpis']['otd'],100)
        self.assertEqual(app.snapshot()['kpis']['incidents'],100)
    def test_invalid_dates(self):
        did=self.delivery()
        with self.assertRaises(ValueError): self.event(did,'Entregue',app.now()+timedelta(hours=1))
        with self.assertRaises(ValueError): self.event(did,'Em rota',app.now()-timedelta(hours=50))
        with self.assertRaises(ValueError):
            app.create_delivery(dict(code='X',client='X',origin='X',destination='X',carrier='X',collected=app.stamp(app.now()),deadline=app.stamp(app.now()-timedelta(hours=1))))
    def test_persistence_and_empty_filter(self):
        self.delivery()
        app.initialize()
        self.assertEqual(app.snapshot()['kpis']['total'],1)
        self.assertEqual(app.snapshot(carrier='Inexistente')['kpis']['total'],0)
        self.assertIsNone(app.snapshot(carrier='Inexistente')['kpis']['incidents'])

    def test_risk_alert_only_before_leaving_for_delivery(self):
        did=self.custom('RISK',60,6)
        self.event(did,'Em rota',app.now()-timedelta(hours=1))
        self.assertEqual(self.codes('RISK'),['risk'])
        self.assertEqual(app.snapshot()['kpis']['at_risk'],1)
        self.assertEqual(app.snapshot()['kpis']['late'],0)
        self.event(did,'Saiu para o cliente',app.now()-timedelta(minutes=30))
        self.assertEqual(self.codes('RISK'),[])
        self.custom('FAR',10,60)
        self.assertEqual(self.codes('FAR'),[])
    def test_severity_and_priority(self):
        self.custom('SEVERE',100,-76)
        mild=self.custom('MILD',30,-6)
        details={d['code']:d for d in app.snapshot()['deliveries']}
        self.assertEqual(details['SEVERE']['alert_details'][0]['severity'],'critical')
        self.assertEqual(details['MILD']['alert_details'][0]['severity'],'high')
        self.assertGreater(details['SEVERE']['priority'],details['MILD']['priority'])
        self.event(mild,'Falta na carga',app.now()-timedelta(hours=1))
        incident=[a for a in next(d for d in app.snapshot()['deliveries'] if d['code']=='MILD')['alert_details'] if a['code']=='incident'][0]
        self.assertEqual(incident['severity'],'critical')
    def test_treated_incident_closes_alert_but_keeps_rate(self):
        did=self.custom('INC',20,40)
        self.event(did,'Avaria',app.now()-timedelta(hours=2))
        self.assertIn('incident',self.codes('INC'))
        self.event(did,'Ocorrência tratada',app.now()-timedelta(hours=1))
        self.assertNotIn('incident',self.codes('INC'))
        self.assertEqual(app.snapshot()['kpis']['incidents'],100)
        with self.assertRaises(ValueError): self.event(did,'Ocorrência tratada',app.now())
    def test_api_filters_scorecard_and_ranking(self):
        a=self.custom('A1',60,-1,'Alfa'); b=self.custom('B1',60,-1,'Beta')
        self.event(a,'Entregue',app.now()-timedelta(minutes=30))
        self.event(b,'Em rota',app.now()-timedelta(hours=1))
        self.assertEqual(app.snapshot(carrier='Alfa')['kpis']['total'],1)
        data=app.snapshot()
        self.assertEqual([c['carrier'] for c in data['carriers']],['Alfa','Beta'])
        self.assertEqual(data['carriers'][0]['rank'],1)
        self.assertEqual(data['carriers'][0]['otd'],0)
    def done(self,code,hours_ago,on_time,carrier='Teste'):
        """Entrega concluída há `hours_ago` horas, no prazo ou 5 h depois dele."""
        did=self.custom(code,hours_ago+20,-(hours_ago-5) if on_time else -(hours_ago+5),carrier)
        self.event(did,'Entregue',app.now()-timedelta(hours=hours_ago))
    def titles(self,data=None):
        return [i['titulo'] for i in (data or app.snapshot())['assistant']['insights']]
    def test_targets_health_and_assistant(self):
        empty=app.snapshot()
        self.assertEqual(empty['health']['level'],None)
        self.assertEqual(empty['assistant']['insights'],[])
        self.assertIn('Não há entregas',empty['assistant']['resumo'])
        did=self.custom('OK',30,40)
        self.event(did,'Em rota',app.now()-timedelta(hours=1))
        self.assertEqual(self.titles(),['Operação sob controle'])
        for i in range(4): self.custom(f'LATE{i}',100,-80)
        data=app.snapshot()
        self.assertEqual(data['kpis']['levels']['late'],'bad')
        self.assertEqual(data['health']['level'],'bad')
        first=data['assistant']['insights'][0]
        self.assertEqual(first['nivel'],'critico')
        self.assertEqual(first['titulo'],'100% dos atrasos vão para Natal')
        self.assertEqual(sorted(first['entregas']),['LATE0','LATE1','LATE2','LATE3'])
        self.assertIn('fora da meta',data['assistant']['resumo'])
        self.assertIn('Prioridade de hoje',data['assistant']['resumo'])
    def test_assistant_simulates_carrier_switch(self):
        for i in range(3): self.done(f'A{i}',30,True,'Alfa')
        for i,ok in enumerate([True,False,False]): self.done(f'B{i}',30,ok,'Beta')
        data=app.snapshot()
        self.assertEqual(round(data['kpis']['otd'],1),66.7)
        sim=next(i for i in data['assistant']['insights'] if i['titulo'].startswith('Simulação'))
        self.assertIn('66,7% para 100%',sim['titulo'])
        self.assertIn('já alcançaria a meta',sim['analise'])
        self.assertEqual(sim['frota'],'Beta')
    def test_assistant_links_open_incidents_to_delays(self):
        did=self.custom('INC',100,-80)
        self.event(did,'Avaria',app.now()-timedelta(hours=90))
        item=next(i for i in app.snapshot()['assistant']['insights'] if 'sem tratativa' in i['titulo'])
        self.assertEqual(item['titulo'],'1 ocorrência sem tratativa, 1 já em atraso')
        self.assertEqual(item['entregas'],['INC'])
        self.event(did,'Ocorrência tratada',app.now()-timedelta(hours=1))
        self.assertFalse(any('sem tratativa' in t for t in self.titles()))
    def test_assistant_detects_recent_drop_in_punctuality(self):
        for i,hours in enumerate([170,146,122,98]): self.done(f'OLD{i}',hours,True)
        for i,hours in enumerate([74,50,26]): self.done(f'NEW{i}',hours,False)
        self.assertIn('Pontualidade caiu nos últimos 3 dias: de 100% para 0%',self.titles())
    def routed(self,code,collected_hours_ago,deadline_hours_from_now,destination='João Pessoa/PB'):
        collected=app.now()-timedelta(hours=collected_hours_ago)
        app.create_delivery(dict(code=code,client='Teste',origin='Recife/PE',destination=destination,carrier='Teste',
            collected=app.stamp(collected),deadline=app.stamp(app.now()+timedelta(hours=deadline_hours_from_now))))
        return next(d['id'] for d in app.snapshot()['deliveries'] if d['code']==code)
    def move(self,did,kind,hours_ago,location):
        app.create_event(dict(delivery_id=did,kind=kind,time=app.stamp(app.now()-timedelta(hours=hours_ago)),location=location))
    def route_of(self,code):
        return next(d for d in app.snapshot()['deliveries'] if d['code']==code)['route']
    def test_trajectory_compares_each_leg_with_plan(self):
        did=self.routed('TRIP',60,12)
        self.move(did,'Em rota',50,'Hub Recife/PE')        # 10 h contra 8 h previstas
        self.move(did,'Em rota',40,'Hub João Pessoa/PB')   # 10 h contra 10 h previstas
        r=self.route_of('TRIP')
        self.assertEqual([s['status'] for s in r['stops']],['done','done','done','current','planned'])
        self.assertEqual([s['excess_hours'] for s in r['stops'][1:3]],[2,0])
        self.assertEqual(r['current_location'],'Hub João Pessoa/PB')
        self.assertEqual(r['bottleneck']['label'],'Hub João Pessoa/PB')   # parado há 40 h; previsto 12 h
        self.assertTrue(r['bottleneck']['open'])
        self.assertAlmostEqual(r['bottleneck']['excess_hours'],28,delta=0.1)
        self.assertAlmostEqual(r['eta_vs_deadline_hours'],-6,delta=0.1)   # sai agora + 6 h de última milha
    def test_trajectory_with_skipped_stops_and_without_plan(self):
        did=self.routed('SKIP',30,40)
        self.move(did,'Entregue',1,'João Pessoa/PB')
        r=self.route_of('SKIP')
        self.assertEqual(r['stops'][-1]['planned_hours'],36)   # trechos pulados somam o previsto
        self.assertIsNone(r['eta'])
        self.assertIsNone(r['bottleneck'])
        did=self.custom('NOPLAN',30,40)
        self.event(did,'Em rota',app.now()-timedelta(hours=20))
        r=self.route_of('NOPLAN')
        self.assertFalse(r['has_plan'])
        self.assertEqual(r['bottleneck']['label'],'Natal')     # trecho mais longo: parada atual
        self.assertEqual(app.snapshot()['bottlenecks'],[])     # sem rota não há "além do previsto"
    def test_assistant_warns_when_eta_passes_deadline_before_it_expires(self):
        did=self.routed('ETA',30,10)                        # prazo em 10 h
        self.move(did,'Em rota',29,'Hub Recife/PE')     # resta Hub JP (10) + saída (12) + entrega (6) = 28 h
        r=self.route_of('ETA')
        self.assertGreater(r['eta_vs_deadline_hours'],0)
        item=next(i for i in app.snapshot()['assistant']['insights'] if 'chegada prevista depois' in i['titulo'])
        self.assertEqual(item['entregas'],['ETA'])
    def test_demo_data_reveals_hub_bottleneck(self):
        app.reset_demo()
        data=app.snapshot()
        self.assertEqual(data['bottlenecks'][0]['label'],'Hub João Pessoa/PB')
        self.assertGreaterEqual(data['bottlenecks'][0]['open'],2)
        self.assertEqual(data['assistant']['insights'][0]['titulo'],'Gargalo do trajeto: Hub João Pessoa/PB')
        delivered=[d for d in data['deliveries'] if d['delivered']]
        self.assertTrue(delivered)
        self.assertTrue(all(d['route']['stops'][-1]['location']==d['destination'] for d in delivered))
    def test_diagnosis_points_where_and_what_to_do(self):
        did=self.routed('HUB',100,-28)                       # prazo vencido há 28 h
        self.move(did,'Em rota',92,'Hub Recife/PE')
        self.move(did,'Em rota',82,'Hub João Pessoa/PB')  # parada no hub há 82 h (previsto 12 h)
        dx=next(d for d in app.snapshot()['deliveries'] if d['code']=='HUB')['diagnosis']
        self.assertEqual(dx['scenario'],'Retenção no hub de destino')
        self.assertEqual(dx['where'],'Hub João Pessoa/PB')
        self.assertEqual(dx['level'],'bad')
        self.assertIn('Prazo vencido',dx['impact'])
        self.assertEqual(len(dx['actions']),3)
        self.move(did,'Avaria',81,'Hub João Pessoa/PB')
        dx=next(d for d in app.snapshot()['deliveries'] if d['code']=='HUB')['diagnosis']
        self.assertEqual(dx['scenario'],'Avaria em Hub João Pessoa/PB')
        self.assertIn('laudo',dx['actions'][0])
    def test_diagnosis_for_transfer_and_late_delivery_but_not_for_on_time(self):
        did=self.routed('TRF',40,30,'Fortaleza/CE')
        self.move(did,'Em rota',32,'Hub Recife/PE')
        self.move(did,'Em rota',20,'Hub Campina Grande/PB')  # trecho até Mossoró: 20 h contra 14 h
        dx=next(d for d in app.snapshot()['deliveries'] if d['code']=='TRF')['diagnosis']
        self.assertEqual(dx['scenario'],'Atraso na transferência entre hubs')
        self.assertEqual(dx['where'],'Hub Campina Grande/PB → Hub Mossoró/RN')
        late=self.routed('LATE',80,-10)
        self.move(late,'Em rota',70,'Hub Recife/PE')
        self.move(late,'Entregue',2,'João Pessoa/PB')
        dx=next(d for d in app.snapshot()['deliveries'] if d['code']=='LATE')['diagnosis']
        self.assertEqual(dx['scenario'],'Entregue com atraso')
        self.assertIn('Hub Recife/PE → João Pessoa/PB',dx['where'])
        ok=self.routed('OK',30,40)
        self.move(ok,'Entregue',1,'João Pessoa/PB')
        self.assertIsNone(next(d for d in app.snapshot()['deliveries'] if d['code']=='OK')['diagnosis'])
    def test_otif_fill_rate_and_fefo(self):
        did=self.custom("P1",30,10)
        with app.session() as con:
            con.execute("INSERT INTO lots VALUES(?,?,?,?)",("G600","OLD","2099-01-01",10))
            con.execute("INSERT INTO lots VALUES(?,?,?,?)",("G600","NEW","2099-06-01",10))
            con.execute("INSERT INTO items(delivery_id,sku,lot,expiry,qty_ordered,qty_delivered) VALUES(?,?,?,?,?,?)",(did,"G600","NEW","2099-06-01",10,8))
        self.event(did,"Entregue",app.now()-timedelta(hours=1))
        data=app.snapshot()
        d=data["deliveries"][0]
        self.assertTrue(d["on_time"]); self.assertFalse(d["complete"]); self.assertFalse(d["otif"])
        k=data["kpis"]
        self.assertEqual((k["otd"],k["otif"],k["fill_rate"],k["fefo"]),(100.0,0.0,80.0,0.0))
        self.assertEqual(k["levels"]["otif"],"bad")
    def test_empties_return_rate_balance_and_alert(self):
        did=self.custom("V1",30,10)
        with app.session() as con:
            con.execute("INSERT INTO items(delivery_id,sku,lot,expiry,qty_ordered,qty_delivered) VALUES(?,?,?,?,?,?)",(did,"G600","L","2099-01-01",20,20))
        app.create_event(dict(delivery_id=did,kind="Entregue",time=app.stamp(app.now()-timedelta(hours=1)),location="Natal",empties="4"))
        open_=self.custom("V2",5,40)
        with app.session() as con:
            con.execute("UPDATE deliveries SET client=? WHERE id IN (?,?)",("Bar do Teste",did,open_))
        data=app.snapshot()
        self.assertEqual((data["kpis"]["empties_out"],data["kpis"]["empties_in"],data["kpis"]["empties"]),(20,4,20.0))
        self.assertEqual(data["empties_balance"][0]["pending"],16)
        self.assertIn("empties",self.codes("V2"))
    def test_demo_lots_flag_expiry(self):
        app.reset_demo()
        data=app.snapshot()
        self.assertIn("bad",{l["level"] for l in data["lots"]})
        k=data["kpis"]
        self.assertLess(k["fill_rate"],100); self.assertLess(k["fefo"],100); self.assertLessEqual(k["otif"],k["otd"])
    def test_demo_data_is_mostly_healthy(self):
        app.reset_demo()
        data=app.snapshot()
        k=data['kpis']
        self.assertEqual(k['total'],app.DEMO_SIZE)
        self.assertGreaterEqual(k['otd'],95)
        self.assertLessEqual(k['late'],3)
        self.assertLessEqual(k['attention'],6)
        flagged=[d for d in data['deliveries'] if d['alert_details'] or (d['delivered'] and not d['on_time'])]
        self.assertTrue(flagged)
        self.assertTrue(all(d['diagnosis'] and d['diagnosis']['where'] and d['diagnosis']['actions'] for d in flagged))
    def test_trend_and_incident_breakdown(self):
        did=self.custom('T1',50,-1)
        self.event(did,'Avaria',app.now()-timedelta(hours=40))
        self.event(did,'Entregue',app.now()-timedelta(hours=30))
        data=app.snapshot()
        self.assertEqual(len(data['trend']),1)
        self.assertEqual(data['trend'][0]['completed'],1)
        self.assertEqual(data['incident_kinds'],[{'kind':'Avaria','count':1}])
    def test_reset_demo_restores_forty_deliveries(self):
        self.delivery()
        app.reset_demo()
        self.assertEqual(app.snapshot()['kpis']['total'],app.DEMO_SIZE)

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=app.DB
        app.DB=Path(self.temp.name)/'http.sqlite3'
        app.initialize()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.port=self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        app.DB=self.old
        self.temp.cleanup()
    def request(self,method,path,body=None,headers=None):
        con=http.client.HTTPConnection('127.0.0.1',self.port)
        con.request(method,path,body=body,headers=headers or {})
        response=con.getresponse(); data=response.read(); con.close()
        return response.status,data
    def post(self,path,payload,headers=None):
        merged={'Content-Type':'application/json'}; merged.update(headers or {})
        return self.request('POST',path,json.dumps(payload),merged)
    def test_data_endpoint_applies_filters(self):
        status,data=self.request('GET','/api/data')
        self.assertEqual(status,200)
        everything=json.loads(data)
        self.assertEqual(everything['kpis']['total'],app.DEMO_SIZE)
        status,data=self.request('GET','/api/data?carrier=Parceira%20Agreste')
        self.assertLess(json.loads(data)['kpis']['total'],app.DEMO_SIZE)
        self.assertEqual({d['carrier'] for d in json.loads(data)['deliveries']},{'Parceira Agreste'})
    def test_static_files_and_blocked_paths(self):
        for path in ['/','/static/app.js','/static/app.css']:
            self.assertEqual(self.request('GET',path)[0],200)
        self.assertEqual(self.request('GET','/static/../app.py')[0],404)
        self.assertEqual(self.request('GET','/static/index.html')[0],404)
    def test_post_validations(self):
        delivery=dict(code='HTTP1',client='C',origin='O',destination='D',carrier='T',collected=app.stamp(app.now()-timedelta(hours=1)),deadline=app.stamp(app.now()+timedelta(hours=40)))
        self.assertEqual(self.post('/api/deliveries',delivery)[0],201)
        self.assertEqual(self.post('/api/deliveries',delivery)[0],400)
        self.assertEqual(self.post('/api/events',dict(delivery_id=9999,kind='Entregue',time=app.stamp(app.now()),location='X'))[0],400)
        self.assertEqual(self.request('POST','/api/deliveries','não é json',{'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.post('/api/inexistente',{})[0],404)
    def test_rejects_foreign_origin_host_and_content_type(self):
        self.assertEqual(self.post('/api/reset',{},{'Origin':'https://site-malicioso.example'})[0],403)
        self.assertEqual(self.request('POST','/api/reset','{}',{'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('GET','/api/data',headers={'Host':'atacante.example'})[0],403)
        self.assertEqual(self.post('/api/reset',{},{'Origin':f'http://localhost:{self.port}'})[0],201)

if __name__=='__main__': unittest.main()
