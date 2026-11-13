import unittest
from fastapi import HTTPException
from backend.delegation import parse_output

STAFF = [{"id":"staff1","name":"Mira","role":"Writer","department":"Marketing","expertise":"Copywriting"}]

class DelegationPlanTests(unittest.TestCase):
    def test_valid_plan(self):
        data={"output":[{"type":"message","content":[{"type":"output_text","text":
            '{"title":"Campaign","jobs":[{"staff_id":"staff1","title":"Draft launch captions","brief":"Write five launch captions for CEO review."}]}'}]}]}
        plan=parse_output(data,STAFF)
        self.assertEqual(plan["jobs"][0]["staff_id"],"staff1")
        self.assertEqual(plan["jobs"][0]["staff_name"],"Mira")

    def test_unknown_staff_rejected(self):
        data={"output":[{"type":"message","content":[{"type":"output_text","text":
            '{"title":"Bad","jobs":[{"staff_id":"stranger","title":"Draft launch captions","brief":"Write five launch captions for CEO review."}]}'}]}]}
        with self.assertRaises(HTTPException):
            parse_output(data,STAFF)

    def test_invalid_json_rejected(self):
        data={"output":[{"type":"message","content":[{"type":"output_text","text":"not-json"}]}]}
        with self.assertRaises(HTTPException):
            parse_output(data,STAFF)

if __name__ == "__main__":
    unittest.main()
