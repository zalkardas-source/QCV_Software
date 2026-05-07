import os
import json
import tempfile
from docling.document_converter import DocumentConverter
from openai import OpenAI
from pydantic import ValidationError
from backend.schemas import CVData

# Initialize Docling converter globally to cache models
_converter = None

def get_docling_converter():
    global _converter
    if _converter is None:
        _converter = DocumentConverter()
    return _converter

def warmup_docling():
    """Initializes the Docling model to avoid delays during the first request."""
    print("Warming up Docling...")
    get_docling_converter()
    print("Docling ready.")

def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """Extracts markdown from a document using Docling."""
    ext = filename.split('.')[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp_file:
        tmp_file.write(file_content)
        tmp_path = tmp_file.name
        
    try:
        print(f"Starting Docling conversion for {filename}...")
        converter = get_docling_converter()
        result = converter.convert(tmp_path)
        print("Conversion successful, exporting to markdown...")
        md = result.document.export_to_markdown()
        print(f"Markdown export successful, length: {len(md)}")
        return md
    except Exception as e:
        print(f"Docling ERROR: {str(e)}")
        raise e
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def structure_cv_data(raw_markdown: str) -> dict:
    """Structures CV Markdown into strict JSON using Minimax-m2.7 and a Pydantic Validation loop."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")
        
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )
    
    # We pass the JSON Schema of our Pydantic model to the LLM
    schema_json = CVData.model_json_schema()
    
    system_prompt = f"""
    You are an expert CV data extractor. Your ONLY job is to extract data from the provided CV markdown and output it as a valid JSON object.
    You MUST adhere EXACTLY to the following JSON schema:
    {json.dumps(schema_json, indent=2)}
    
    CRITICAL INSTRUCTIONS:
    - Output ONLY valid JSON, no markdown blocks.
    - Personal Details: Full name, DOB, Nationality, Email, Phone, Location (City, Country).
    - Skill Matrix: 
        * SCAN THE ENTIRE DOCUMENT for 'Skills', 'Competences', 'Expertise', 'IT Knowledge'.
        * ESPECIALLY LOOK FOR TABLES or lists under headers like 'SAP COMPETENCES'.
        * RATINGS: If you see visual ratings (dots ●●●, circles ○○○, stars ***, X's [X][X][ ], or numbers 8/10), convert them to a 1-5 numeric rating.
        * Example: 'SAP PP ●●●●●●●●●●' (10 dots) = 5. 'SAP QM ●●●●●●○○○○' (6 dots) = 3.
        * If a skill name has symbols next to it, those are almost certainly ratings.
        * IMPORTANT: Extract EVERY skill found. Do not summarize.
    - Projects/Work Experience: 
        * Extract EVERY job and project found. 
        * Map 'Work Experience' or 'Employment History' to the projects array.
        * Maintain the original language for descriptions.
    """
    
    # DEBUG: See what Docling actually finds
    print(f"--- DEBUG: RAW CV MARKDOWN START ---\n{raw_markdown}\n--- DEBUG: RAW CV MARKDOWN END ---")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"CV Text:\n{raw_markdown}"}
    ]
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Extraction attempt {attempt + 1}/{max_retries}...")
            response = client.chat.completions.create(
                model="minimax/minimax-m2.7",
                messages=messages,
                temperature=0.1
            )
            
            text_content = response.choices[0].message.content.strip()
            
            # Clean up potential markdown formatting just in case
            if text_content.startswith("```json"):
                text_content = text_content[7:-3].strip()
            elif text_content.startswith("```"):
                text_content = text_content[3:-3].strip()
                
            parsed_json = json.loads(text_content)
            
            # VALIDATION STEP: The Mold checks the data
            validated_data = CVData(**parsed_json)
            
            print("Validation successful!")
            # Return as dict for the database
            return validated_data.model_dump()
            
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"Validation failed on attempt {attempt + 1}: {str(e)}")
            if attempt == max_retries - 1:
                # If it's the last attempt, raise the error so the backend doesn't silently fail
                raise ValueError(f"Failed to extract structured data after {max_retries} attempts. Last error: {str(e)}")
                
            # THE SELF-HEALING LOOP: Feed the error back to the LLM
            # The assistant's incorrect output
            try:
                # Try to append the bad text content if it exists
                messages.append({"role": "assistant", "content": text_content})
            except UnboundLocalError:
                pass 
                
            error_message = f"Your previous output failed validation. Please correct your output and return only valid JSON.\nError details: {str(e)}"
            messages.append({"role": "user", "content": error_message})
