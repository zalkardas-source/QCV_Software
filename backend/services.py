import os
import sys
import json
import tempfile

# Windows DLL Fix for PyTorch/Docling
if os.name == 'nt':
    try:
        import site
        for path in site.getsitepackages():
            torch_lib_path = os.path.join(path, 'torch', 'lib')
            if os.path.exists(torch_lib_path):
                # Only add if it's a valid directory to avoid Errno 22
                os.add_dll_directory(os.path.abspath(torch_lib_path))
    except Exception:
        pass

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions
from openai import OpenAI
from pydantic import ValidationError
from backend.schemas import CVData

# Initialize Docling converter globally to cache models
_converter = None

def get_docling_converter():
    global _converter
    if _converter is None:
        # Optimization: Disable heavy features not needed for CV text extraction
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True 
        pipeline_options.do_table_structure = True
        pipeline_options.images_scale = 0.0 
        
        _converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    return _converter

def warmup_docling():
    """Initializes the Docling model to avoid delays during the first request."""
    print("Warming up Docling Engine...")
    try:
        get_docling_converter()
        print("Docling Engine optimized and ready.")
    except Exception as e:
        print(f"Warmup ERROR: {e}")

def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """Extracts markdown from a document using Docling."""
    ext = filename.split('.')[-1].lower() if "." in filename else "pdf"
    import uuid
    tmp_path = os.path.abspath(f"tmp_{uuid.uuid4().hex}.{ext}")
    
    try:
        with open(tmp_path, 'wb') as f:
            f.write(file_content)
        
        converter = get_docling_converter()
        # Convert with optimized pipeline
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    except Exception as e:
        print(f"Docling ERROR: {type(e).__name__}: {str(e)}")
        raise e
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

def structure_cv_data(raw_markdown: str) -> dict:
    """Structures CV Markdown into strict JSON using Minimax-m2.7 with optimized prompt."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key: raise ValueError("OPENROUTER_API_KEY is not set")
        
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    schema_json = CVData.model_json_schema()
    
    # Optimized prompt: Shorter, more direct, better for lower latency
    system_prompt = f"""
    Return ONLY a valid JSON object matching this schema:
    {json.dumps(schema_json)}
    
    RULES:
    1. Skills: Extract ALL skills. Categorize logically (e.g. 'SAP', 'Dev', 'Soft Skills').
    2. Ratings: Scale 1-10. (Expert=10, Senior=8-9, Mid=6-7, Junior=4-5, Basic=1-3).
    3. Experience: Extract every job/project. Maintain original language for descriptions.
    4. Format: No preamble, no markdown blocks, just raw JSON.
    """
    
    # DEBUG: See what Docling actually finds
    print(f"--- DEBUG: RAW CV MARKDOWN (first 500 chars) ---\n{raw_markdown[:500]}\n--- END ---")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"CV Text:\n{raw_markdown}"}
    ]
    
    max_retries = 3
    for attempt in range(max_retries):
        text_content = None
        try:
            print(f"[LLM] Extraction attempt {attempt + 1}/{max_retries}...")
            response = client.chat.completions.create(
                model="minimax/minimax-m2.7",
                messages=messages,
                temperature=0.1
            )
            
            print(f"[LLM] Response received. Choices: {len(response.choices)}")
            
            if not response.choices or not response.choices[0].message.content:
                print(f"[LLM] ERROR: Empty response from API! Full response: {response}")
                raise ValueError("LLM returned an empty response. The model may be unavailable.")
            
            text_content = response.choices[0].message.content.strip()
            print(f"[LLM] Raw response length: {len(text_content)}")
            print(f"[LLM] First 200 chars: {text_content[:200]}")
            
            # Clean up potential markdown formatting just in case
            if text_content.startswith("```json"):
                text_content = text_content[7:-3].strip()
            elif text_content.startswith("```"):
                text_content = text_content[3:-3].strip()
                
            parsed_json = json.loads(text_content)
            
            # VALIDATION STEP: The Mold checks the data
            validated_data = CVData(**parsed_json)
            
            print("[LLM] Validation successful!")
            # Return as dict for the database
            return validated_data.model_dump()
            
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"[LLM] Validation failed on attempt {attempt + 1}: {str(e)}")
            if attempt == max_retries - 1:
                raise ValueError(f"Failed to extract structured data after {max_retries} attempts. Last error: {str(e)}")
                
            # THE SELF-HEALING LOOP: Feed the error back to the LLM
            if text_content:
                messages.append({"role": "assistant", "content": text_content})
                
            error_message = f"Your previous output failed validation. Please correct your output and return only valid JSON.\nError details: {str(e)}"
            messages.append({"role": "user", "content": error_message})
            
        except Exception as e:
            # Catch API errors, timeouts, auth failures, etc.
            print(f"[LLM] UNEXPECTED ERROR on attempt {attempt + 1}: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            if attempt == max_retries - 1:
                raise ValueError(f"LLM API error: {type(e).__name__}: {str(e)}")
