"""Integration tests for editable AI tasks, priorities, tomorrow, recurrence and monthly Director plans."""
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import office_db, task_schedule, monthly
from backend.main import app

class TaskCentreTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        login=self.client.post('/api/login',json={'password':os.environ['CEO_PASSWORD']})
        self.assertEqual(login.status_code,200,login.text)
        self.tasks=[]
        self.series=[]
        with office_db.connect() as db:
            director=db.execute("""SELECT id FROM staff WHERE org_role='Director'
                AND department='Creative' LIMIT 1""").fetchone()
            self.assertIsNotNone(director)
            self.director_id=director['id']

    def tearDown(self):
        with office_db.connect() as db:
            for series_id in self.series:
                db.execute('DELETE FROM tasks WHERE series_id=%s',(series_id,))
                db.execute('DELETE FROM task_series WHERE id=%s',(series_id,))
            for task_id in self.tasks:
                db.execute('DELETE FROM tasks WHERE id=%s',(task_id,))
        self.client.close()

    def create(self,**fields):
        data={'title':'CI Draft Creative Brief','description':'Write a detailed internal draft with five sections.',
              'assignee':self.director_id,'priority':'High',
              'due_date':(task_schedule.local_today()+timedelta(days=1)).isoformat(),
              'recurrence':'None'}
        data.update(fields)
        result=self.client.post('/api/tasks',json=data)
        self.assertEqual(result.status_code,200,result.text)
        self.tasks.append(result.json()['id'])
        if result.json().get('series_id'):
            self.series.append(result.json()['series_id'])
        return result.json()['id'],data

    def test_edit_assigned_task_priority_description_before_ai_run(self):
        task_id,body=self.create()
        body.update({'title':'Updated AI editorial brief',
                     'description':'Produce three Malaysian regional campaign concepts with sources.',
                     'priority':'Urgent'})
        revised=self.client.put('/api/tasks/'+task_id,json=body)
        self.assertEqual(revised.status_code,200,revised.text)
        item=next(t for t in self.client.get('/api/office').json()['tasks'] if t['id']==task_id)
        self.assertEqual(item['priority'],'Urgent')
        self.assertEqual(item['description'],body['description'])
        self.assertEqual(str(item['due_date']),body['due_date'])

        with patch('backend.main.office_workforce.execute',
                   return_value={'id':'test-ai-result','status':'For approval','result':'Draft'}) as run:
            executed=self.client.post('/api/workforce/run/'+task_id)
        self.assertEqual(executed.status_code,200,executed.text)
        ai_task=run.call_args.args[0]
        self.assertIn(body['description'],ai_task['brief'])
        self.assertIn('Priority: Urgent',ai_task['brief'])
        self.assertIn(body['due_date'],ai_task['brief'])
        self.assertEqual(self.client.put('/api/tasks/'+task_id,json=body).status_code,409)
        self.assertEqual(self.client.post('/api/workforce/run/'+task_id).status_code,409)

    def test_daily_recurrence_does_not_duplicate(self):
        start=task_schedule.local_today()
        task_id,_=self.create(recurrence='Daily',due_date=start.isoformat())
        with office_db.connect() as db:
            first=task_schedule.generate_due(db,start+timedelta(days=1))
            second=task_schedule.generate_due(db,start+timedelta(days=1))
            all_tasks=db.execute('SELECT id,due_date FROM tasks WHERE series_id=%s',
                                 (self.series[0],)).fetchall()
        self.assertGreaterEqual(first['created'],1)
        self.assertEqual(second['created'],0)
        self.assertEqual(len(all_tasks),2)
        self.assertEqual({x['due_date'] for x in all_tasks},
                         {start,start+timedelta(days=1)})

    def test_monthly_director_workplan_idempotence(self):
        with office_db.connect() as db:
            db.execute("SAVEPOINT ci_monthly")
            try:
                first=monthly.generate_month(db,'2098-08')
                second=monthly.generate_month(db,'2098-08')
                rows=db.execute("""SELECT t.description,t.due_date,t.recurrence
                    FROM tasks t JOIN monthly_assignments m ON m.task_id=t.id
                    WHERE m.period='2098-08'""").fetchall()
                self.assertEqual(second['created'],0)
                self.assertGreaterEqual(first['created'],9)
                self.assertEqual(first['created'],len(rows))
                self.assertTrue(all(r['description'] and r['due_date'] and
                                    r['recurrence']=='Monthly' for r in rows))
            finally:
                db.execute("ROLLBACK TO SAVEPOINT ci_monthly")

    def test_remove_unrun_task_hides_it_and_preserves_audit(self):
        task_id,_=self.create()
        response=self.client.delete('/api/tasks/'+task_id)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['recurrence_stopped'])
        listing=self.client.get('/api/office').json()['tasks']
        self.assertFalse(any(item['id']==task_id for item in listing))
        with office_db.connect() as db:
            row=db.execute('SELECT deleted_at FROM tasks WHERE id=%s',(task_id,)).fetchone()
            self.assertIsNotNone(row['deleted_at'])
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM audit WHERE action='task.removed' AND detail LIKE %s",
                (task_id+'%',)).fetchone())
        self.assertEqual(self.client.delete('/api/tasks/'+task_id).status_code,404)
        self.assertEqual(self.client.post('/api/workforce/run/'+task_id).status_code,404)

    def test_remove_recurring_origin_stops_future_occurrences(self):
        today=task_schedule.local_today()
        task_id,_=self.create(recurrence='Daily',due_date=today.isoformat())
        response=self.client.delete('/api/tasks/'+task_id)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['recurrence_stopped'])
        with office_db.connect() as db:
            series=db.execute('SELECT enabled FROM task_series WHERE id=%s',
                              (self.series[0],)).fetchone()
            self.assertFalse(series['enabled'])
            result=task_schedule.generate_due(db,today+timedelta(days=2))
        self.assertEqual(result['created'],0)

    def test_remove_one_recurring_occurrence_keeps_schedule(self):
        today=task_schedule.local_today()
        task_id,_=self.create(recurrence='Daily',due_date=today.isoformat())
        with office_db.connect() as db:
            task_schedule.generate_due(db,today+timedelta(days=1))
            occurrence=db.execute("""SELECT id FROM tasks WHERE series_id=%s
                AND occurrence_date=%s""",(self.series[0],today+timedelta(days=1))).fetchone()
        self.assertIsNotNone(occurrence)
        result=self.client.delete('/api/tasks/'+occurrence['id'])
        self.assertEqual(result.status_code,200,result.text)
        self.assertFalse(result.json()['recurrence_stopped'])
        with office_db.connect() as db:
            self.assertTrue(db.execute(
                'SELECT enabled FROM task_series WHERE id=%s',
                (self.series[0],)).fetchone()['enabled'])
            task_schedule.generate_due(db,today+timedelta(days=2))
            future=db.execute("""SELECT id FROM tasks WHERE series_id=%s
                AND occurrence_date=%s AND deleted_at IS NULL""",
                (self.series[0],today+timedelta(days=2))).fetchone()
            self.assertIsNotNone(future)
        self.tasks.append(occurrence['id'])

    def test_removed_monthly_assignment_not_regenerated(self):
        with office_db.connect() as db:
            db.execute("SAVEPOINT test_monthly_removal")
            try:
                first=monthly.generate_month(db,'2098-08')
                self.assertGreaterEqual(first['created'],9)
                row=db.execute("""SELECT m.task_id FROM monthly_assignments m
                    JOIN tasks t ON t.id=m.task_id
                    WHERE m.period='2098-08' LIMIT 1""").fetchone()
                result=task_schedule.remove(db,row['task_id'])
                self.assertTrue(result['ok'])
                self.assertFalse(result['recurrence_stopped'])
                after=monthly.generate_month(db,'2098-08')
                self.assertEqual(after['created'],0)
                report=monthly.summary(db,'2098-08')
                self.assertFalse(any(x['task_id']==row['task_id']
                    for x in report['assignments']))
                self.assertEqual(db.execute("""SELECT COUNT(*) AS n
                    FROM monthly_assignments WHERE period='2098-08'""").fetchone()['n'],
                    first['created'])
            finally:
                db.execute("ROLLBACK TO SAVEPOINT test_monthly_removal")

    def test_cannot_remove_task_after_run_ai(self):
        task_id,_=self.create()
        with patch('backend.main.office_workforce.execute',
                   return_value={'id':'test-ai-result','status':'For approval','result':'Draft'}):
            executed=self.client.post('/api/workforce/run/'+task_id)
        self.assertEqual(executed.status_code,200,executed.text)
        self.assertEqual(self.client.delete('/api/tasks/'+task_id).status_code,409)

    def test_invalid_priority_and_missing_recurring_date_are_rejected(self):
        bad=self.client.post('/api/tasks',json={
            'title':'Invalid priority','description':'Detailed brief',
            'priority':'Super urgent','recurrence':'None'})
        self.assertEqual(bad.status_code,400)
        missing=self.client.post('/api/tasks',json={
            'title':'Missing recurring date','description':'Detailed brief',
            'priority':'Normal','recurrence':'Weekly'})
        self.assertEqual(missing.status_code,400)

if __name__=='__main__':
    unittest.main()
