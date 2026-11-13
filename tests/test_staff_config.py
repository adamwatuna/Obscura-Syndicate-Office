"""CEO staff configuration CRUD and safe delete integration tests."""
import os
import unittest
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.main import app

class StaffConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        response=self.client.post('/api/login',json={'password':os.environ['CEO_PASSWORD']})
        self.assertEqual(response.status_code,200,response.text)
    def tearDown(self):
        self.client.close()

    def test_staff_config_private_editable_and_deletable(self):
        marker="Staff-CI-"+uuid4().hex[:12]
        expert="Internal CEO expertise and instructions "+uuid4().hex
        data={'name':marker,'role':'Creative Director','department':'Creative',
              'expertise':expert,'autonomy':'Approval required',
              'tools':'Premiere (requested only)'}
        created=self.client.post('/api/staff',json=data)
        self.assertEqual(created.status_code,200,created.text)
        staff_id=created.json()['id']
        task_id=None
        try:
            staff_row=next(s for s in self.client.get('/api/office').json()['staff']
                           if s['id']==staff_id)
            self.assertNotIn('expertise',staff_row)
            self.assertNotIn('tools',staff_row)
            self.assertNotIn('autonomy',staff_row)
            restricted=TestClient(app).get(f'/api/staff/{staff_id}/configuration')
            self.assertEqual(restricted.status_code,401)
            config=self.client.get(f'/api/staff/{staff_id}/configuration')
            self.assertEqual(config.status_code,200)
            self.assertEqual(config.json()['staff']['expertise'],expert)

            data['role']='Executive Creative Director'
            data['expertise']=expert+' UPDATED'
            edited=self.client.put(f'/api/staff/{staff_id}',json=data)
            self.assertEqual(edited.status_code,200,edited.text)
            self.assertEqual(self.client.get(f'/api/staff/{staff_id}/configuration')
                             .json()['staff']['role'],'Executive Creative Director')
            self.assertEqual(self.client.get(f'/api/staff/{staff_id}/configuration')
                             .json()['staff']['expertise'],expert+' UPDATED')

            task=self.client.post('/api/tasks',json={'title':marker+' creative draft','assignee':staff_id})
            self.assertEqual(task.status_code,200,task.text)
            task_id=task.json()['id']

            deleted=self.client.delete(f'/api/staff/{staff_id}')
            self.assertEqual(deleted.status_code,200,deleted.text)
            self.assertEqual(deleted.json()['unassigned_tasks'],1)
            listing=self.client.get('/api/office').json()
            self.assertFalse(any(s['id']==staff_id for s in listing['staff']))
            old_task=next(t for t in listing['tasks'] if t['id']==task_id)
            self.assertEqual(old_task['assignee'],'')
            self.assertEqual(self.client.get(f'/api/staff/{staff_id}/configuration').status_code,404)
        finally:
            # Cleanup only the CI test fixture and leave any unrelated data alone.
            from backend import office_db
            with office_db.connect() as db:
                if task_id:
                    db.execute('DELETE FROM tasks WHERE id=%s',(task_id,))
                db.execute('DELETE FROM staff WHERE id=%s',(staff_id,))

if __name__=='__main__':
    unittest.main()
