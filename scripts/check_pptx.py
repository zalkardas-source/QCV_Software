import sys
import os
sys.path.append(os.getcwd())

from backend.export_pptx import create_pptx_summary

test_data = {
    "personal_information": {"full_name": "Test Candidate"},
    "small_summary": "This is a test summary.",
    "skill_matrix": [
        {"category": "Test Cat", "skills": [{"skill": "Skill 1", "rating": 10}]}
    ],
    "projects": [
        {"name": "Project 1", "duration": "2020-2021", "description": "Desc"}
    ]
}

try:
    print("Testing PPTX export...")
    pptx_bytes = create_pptx_summary(test_data)
    print(f"Success! PPTX size: {len(pptx_bytes)} bytes")
except Exception as e:
    import traceback
    print("PPTX EXPORT FAILED:")
    traceback.print_exc()
