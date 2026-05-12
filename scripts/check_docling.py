
import sys
import os

# Add the project root to sys.path to find backend
sys.path.append(os.getcwd())

try:
    from docling.document_converter import DocumentConverter
    print("Docling imported successfully")
    converter = DocumentConverter()
    print("Converter initialized")
    # Try to convert a dummy file or just check if it's alive
except Exception as e:
    print(f"Error: {e}")
