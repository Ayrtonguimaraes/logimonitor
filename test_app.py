import tempfile
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
        with app.connect() as con:
            con.execute('DELETE FROM events')
            con.execute('DELETE FROM deliveries')
    def tearDown(self):
        app.DB=self.old
        self.temp.cleanup()
    def delivery(self,code='TEST',hours=48):
        collected=app.now()-timedelta(hours=hours)
        app.create_delivery(dict(code=code,client='Teste',origin='Recife',destination='Natal',carrier='Teste',collected=app.stamp(collected),deadline=app.stamp(collected+timedelta(hours=24))))
        return app.snapshot()['deliveries'][-1]['id']
    def event(self,did,kind,when):
        app.create_event(dict(delivery_id=did,kind=kind,time=app.stamp(when),location='Natal'))
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
        with self.assertRaises(ValueError): self.event(did,'Em trânsito',app.now())
    def test_punctual_and_incidents_count_once(self):
        did=self.delivery()
        self.event(did,'Avaria',app.now()-timedelta(hours=46))
        self.event(did,'Tentativa sem sucesso',app.now()-timedelta(hours=45))
        self.event(did,'Entregue',app.now()-timedelta(hours=24))
        self.assertEqual(app.snapshot()['kpis']['otd'],100)
        self.assertEqual(app.snapshot()['kpis']['incidents'],100)
    def test_invalid_dates(self):
        did=self.delivery()
        with self.assertRaises(ValueError): self.event(did,'Entregue',app.now()+timedelta(hours=1))
        with self.assertRaises(ValueError): self.event(did,'Em trânsito',app.now()-timedelta(hours=50))
        with self.assertRaises(ValueError):
            app.create_delivery(dict(code='X',client='X',origin='X',destination='X',carrier='X',collected=app.stamp(app.now()),deadline=app.stamp(app.now()-timedelta(hours=1))))
    def test_persistence_and_empty_filter(self):
        self.delivery()
        app.initialize()
        self.assertEqual(app.snapshot()['kpis']['total'],1)
        self.assertEqual(app.snapshot(carrier='Inexistente')['kpis']['total'],0)
        self.assertIsNone(app.snapshot(carrier='Inexistente')['kpis']['incidents'])

if __name__=='__main__': unittest.main()
