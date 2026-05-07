import streamlit as st
import os
import io
import json

# Windows DLL Fix for PyTorch/Docling
if os.name == 'nt':
    try:
        import site
        for path in site.getsitepackages():
            torch_lib_path = os.path.join(path, 'torch', 'lib')
            if os.path.exists(torch_lib_path):
                os.add_dll_directory(torch_lib_path)
    except Exception:
        pass

from fpdf import FPDF
import requests

# Page configuration
st.set_page_config(page_title="AI CV Parser", page_icon="📄", layout="wide")

# Custom CSS for Premium Light Mode
css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Hide default streamlit header/footer for cleaner look */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* Hero Section */
.hero-container {
    padding: 2rem 1rem;
    text-align: center;
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    border-radius: 16px;
    margin-bottom: 2rem;
    border: 1px solid #cbd5e1;
}
.hero-title {
    font-size: 2.5rem;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 0.5rem;
}
.hero-subtitle {
    font-size: 1.1rem;
    color: #475569;
}

/* Custom Cards */
.custom-card {
    background-color: #ffffff;
    border-radius: 12px;
    padding: 1.5rem;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
    margin-bottom: 1.5rem;
    border: 1px solid #e2e8f0;
}
.card-header {
    font-size: 1.25rem;
    font-weight: 600;
    color: #1e293b;
    margin-bottom: 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #f1f5f9;
}
.info-row {
    margin-bottom: 0.5rem;
    color: #475569;
}
.info-icon {
    margin-right: 0.5rem;
}

/* Badges & Stars */
.skill-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.75rem;
    background-color: #f8fafc;
    border-radius: 8px;
    margin-bottom: 0.5rem;
}
.skill-name {
    font-weight: 600;
    color: #334155;
}
.stars {
    color: #fbbf24;
    letter-spacing: 2px;
}
.skill-raw {
    color: #64748b;
    font-size: 0.9rem;
}

/* Projects */
.project-card {
    padding: 1rem;
    border-left: 4px solid #3b82f6;
    background-color: #f8fafc;
    border-radius: 0 8px 8px 0;
    margin-bottom: 1rem;
}
.project-title {
    font-weight: 700;
    color: #1e293b;
    font-size: 1.1rem;
}
.project-duration {
    font-size: 0.85rem;
    color: #64748b;
    margin-bottom: 0.5rem;
}
.project-desc {
    color: #475569;
    line-height: 1.5;
}
</style>
"""
st.markdown(css, unsafe_allow_html=True)

def upload_to_backend(uploaded_file):
    """Sends the uploaded file to the FastAPI backend for processing."""
    try:
        # 1. Background login to get token (using default admin credentials)
        login_res = requests.post(
            "http://localhost:8000/api/login",
            data={"username": "admin@quatelio.com", "password": "admin123"},
            timeout=5
        )
        if login_res.status_code != 200:
            st.error("Failed to authenticate with Backend.")
            return None
            
        token = login_res.json().get("access_token")
        
        # 2. Upload file to backend
        headers = {"Authorization": f"Bearer {token}"}
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
        
        with st.spinner("Sending to Backend Engine for AI Analysis & Validation..."):
            parse_res = requests.post(
                "http://localhost:8000/api/parse-cv",
                files=files,
                headers=headers,
                timeout=120 # CV processing can take a bit
            )
            
            if parse_res.status_code != 200:
                st.error(f"Backend Error: {parse_res.text}")
                return None
                
            return parse_res.json().get("data")
            
    except requests.exceptions.ConnectionError:
        st.error("Could not connect to backend. Is the FastAPI server running on port 8000?")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None

def create_pdf_summary(data):
    """Generates a premium, professional PDF summary."""
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=20)
        
        # --- Helper for encoding ---
        def safe_txt(s):
            if not s: return ""
            # Replace common non-latin-1 characters
            s = str(s).replace("\u2013", "-").replace("\u2014", "-").replace("\u2022", "*")
            return s.encode('latin-1', 'replace').decode('latin-1')

        # --- Colors ---
        PRIMARY_COLOR = (15, 23, 42)    # Dark Blue / Slate
        SECONDARY_COLOR = (51, 65, 85)  # Medium Blue/Grey
        ACCENT_COLOR = (59, 130, 246)   # Bright Blue
        
        # --- Header ---
        pdf.set_fill_color(*PRIMARY_COLOR)
        pdf.rect(0, 0, 210, 40, 'F')
        
        pdf.set_y(15)
        pdf.set_font("helvetica", "B", 24)
        pdf.set_text_color(255, 255, 255)
        personal = data.get("personal_information", {})
        pdf.cell(0, 10, txt=safe_txt(personal.get("full_name", "CV SUMMARY")), ln=True, align="C")
        
        # Contact Line under Name
        pdf.set_font("helvetica", size=10)
        contact_bits = []
        if personal.get("email"): contact_bits.append(personal.get("email"))
        if personal.get("phone"): contact_bits.append(personal.get("phone"))
        if personal.get("location"): contact_bits.append(personal.get("location"))
        
        pdf.set_y(28)
        if contact_bits:
            pdf.cell(0, 5, txt=safe_txt("  |  ".join(contact_bits)), ln=True, align="C")
        
        pdf.set_y(45)
        pdf.set_text_color(0, 0, 0) # Reset to black
        
        # --- Professional Summary ---
        summary = data.get("small_summary", "")
        if summary:
            pdf.set_font("helvetica", "B", 14)
            pdf.set_text_color(*ACCENT_COLOR)
            pdf.cell(0, 10, txt="Professional Summary", ln=True)
            pdf.set_text_color(*SECONDARY_COLOR)
            pdf.set_font("helvetica", size=11)
            pdf.multi_cell(0, 6, txt=safe_txt(summary))
            pdf.ln(8)
            
        # --- Skills ---
        skills = data.get("skill_matrix", [])
        if skills:
            pdf.set_font("helvetica", "B", 14)
            pdf.set_text_color(*ACCENT_COLOR)
            pdf.cell(0, 10, txt="Skills & Expertise", ln=True)
            pdf.set_text_color(*SECONDARY_COLOR)
            
            # Two columns for skills to save space
            pdf.set_font("helvetica", size=11)
            col_width = 90
            for i, skill in enumerate(skills):
                s_name = skill.get("skill", "")
                s_rating = skill.get("rating", "")
                text = f" {s_name}"
                if s_rating: text += f" ({s_rating}/5)"
                
                # Simple bullet point replacement
                pdf.set_text_color(*ACCENT_COLOR)
                pdf.cell(5, 7, txt=">", ln=0)
                pdf.set_text_color(*SECONDARY_COLOR)
                pdf.cell(col_width, 7, txt=safe_txt(text), ln=True)
            pdf.ln(8)
            
        # --- Projects & Experience ---
        projects = data.get("projects", [])
        if projects:
            pdf.set_font("helvetica", "B", 14)
            pdf.set_text_color(*ACCENT_COLOR)
            pdf.cell(0, 10, txt="Work Experience & Projects", ln=True)
            
            for proj in projects:
                # Project Header
                pdf.set_font("helvetica", "B", 12)
                pdf.set_text_color(*PRIMARY_COLOR)
                p_name = proj.get("name", "Position/Project")
                pdf.cell(0, 8, txt=safe_txt(p_name), ln=True)
                
                # Duration
                p_dur = proj.get("duration", "")
                if p_dur:
                    pdf.set_font("helvetica", "I", 10)
                    pdf.set_text_color(100, 100, 100)
                    pdf.cell(0, 5, txt=safe_txt(p_dur), ln=True)
                
                # Description
                pdf.set_font("helvetica", size=10)
                pdf.set_text_color(*SECONDARY_COLOR)
                pdf.multi_cell(0, 5, txt=safe_txt(proj.get("description", "")))
                pdf.ln(5)
                
        # --- Footer ---
        pdf.set_y(-15)
        pdf.set_font("helvetica", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 10, txt="Generated by AI CV Parser", align="C")
                
        return bytes(pdf.output())
    except Exception as e:
        # Return a simple error PDF if something goes wrong
        error_pdf = FPDF()
        error_pdf.add_page()
        error_pdf.set_font("helvetica", size=12)
        error_pdf.cell(200, 10, txt=f"Error generating PDF: {str(e)}", ln=True)
        return bytes(error_pdf.output())

# Sidebar
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3135/3135692.png", width=60)
    st.markdown("### AI Parser Settings")
    st.info("The system extracts text locally and uses Minimax-m2.7 (via OpenRouter) for logic routing.")
    st.markdown("---")
    st.markdown("Built with ❤️ using Streamlit")

# Main content Header
st.markdown("""
<div class='hero-container'>
    <div class='hero-title'>📄 Premium CV Parser</div>
    <div class='hero-subtitle'>Upload a resume document (PDF/Word/TXT) to magically extract and structure all details.</div>
</div>
""", unsafe_allow_html=True)

uploaded_file = st.file_uploader("Drop your CV document here...", type=["pdf", "docx", "txt"])

if uploaded_file is not None:
    # Reset session state if a new file is uploaded
    if "last_uploaded_file" not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.parsed_cv_data = None
        st.session_state.last_uploaded_file = uploaded_file.name

    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.markdown("<div class='custom-card'><div class='card-header'>Original Document</div>", unsafe_allow_html=True)
        st.info(f"File: {uploaded_file.name}")
        st.markdown("</div>", unsafe_allow_html=True)
    
    with col2:
        if "parsed_cv_data" not in st.session_state:
            st.session_state.parsed_cv_data = None
            
        if st.button("Start AI Analysis", type="primary"):
            cv_data = upload_to_backend(uploaded_file)
            if cv_data:
                st.session_state.parsed_cv_data = cv_data
        
        if st.session_state.parsed_cv_data:
            data = st.session_state.parsed_cv_data
            
            # Debug: Show raw JSON (can be hidden)
            with st.expander("🔍 View Raw Analysis Data (Debug)"):
                st.json(data)
                
            st.toast('Analysis complete!', icon='✅')
            
            personal_info = data.get("personal_information", {})
            name = personal_info.get("full_name") or personal_info.get("name", "Unknown Name")
            age_dob = personal_info.get("age_or_dob") or personal_info.get("age") or personal_info.get("date_of_birth", "")
            nationality = personal_info.get("nationality", "")
            marital = personal_info.get("marital_status") or personal_info.get("marital", "")
            email = personal_info.get("email", "")
            phone = personal_info.get("phone", "")
            location = personal_info.get("location") or personal_info.get("address", "")
            linkedin = personal_info.get("linkedin", "")
            website = personal_info.get("website") or personal_info.get("url", "")
            
            # Profile Card (Built dynamically without linebreaks to prevent Markdown code block escaping)
            html_profile = "<div class='custom-card'>"
            html_profile += f"<h2 style='color:#0f172a; margin-bottom: 1rem;'>{name}</h2>"
            html_profile += "<div style='display:flex; flex-wrap:wrap; gap: 1rem;'>"
            if age_dob: html_profile += f"<div class='info-row'><span class='info-icon'>🎂</span>{age_dob}</div>"
            if nationality: html_profile += f"<div class='info-row'><span class='info-icon'>🌍</span>{nationality}</div>"
            if marital: html_profile += f"<div class='info-row'><span class='info-icon'>💍</span>{marital}</div>"
            if email: html_profile += f"<div class='info-row'><span class='info-icon'>📧</span>{email}</div>"
            if phone: html_profile += f"<div class='info-row'><span class='info-icon'>📞</span>{phone}</div>"
            if location: html_profile += f"<div class='info-row'><span class='info-icon'>📍</span>{location}</div>"
            if linkedin: html_profile += f"<div class='info-row'><span class='info-icon'>🔗</span>{linkedin}</div>"
            if website: html_profile += f"<div class='info-row'><span class='info-icon'>🌐</span>{website}</div>"
            html_profile += "</div></div>"
            st.markdown(html_profile, unsafe_allow_html=True)
            
            # Summary Card
            summary = data.get("small_summary", "")
            if summary:
                html_summary = "<div class='custom-card'>"
                html_summary += "<div class='card-header'>Executive Summary</div>"
                html_summary += f"<div style='color:#475569; font-style:italic; line-height: 1.6;'>\"{summary}\"</div>"
                html_summary += "</div>"
                st.markdown(html_summary, unsafe_allow_html=True)
            
            # Tabs for deeper data
            tab1, tab2 = st.tabs(["💡 Skill Matrix", "🚀 Projects & Experience"])
            
            with tab1:
                st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
                skills = data.get("skill_matrix", [])
                if skills:
                    for skill in skills:
                        name = skill.get("skill", "Unknown Skill")
                        rating_raw = skill.get("rating", "")
                        
                        html_skill = f"<div class='skill-row'><span class='skill-name'>{name}</span>"
                        if rating_raw:
                            try:
                                rating = int(rating_raw)
                                stars = "★" * rating + "☆" * (5 - rating)
                                html_skill += f"<span class='stars'>{stars}</span>"
                            except ValueError:
                                html_skill += f"<span class='skill-raw'>{rating_raw}</span>"
                        html_skill += "</div>"
                        st.markdown(html_skill, unsafe_allow_html=True)
                else:
                    st.info("No skills detected.")
                st.markdown("</div>", unsafe_allow_html=True)
            
            with tab2:
                st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
                projects = data.get("projects", [])
                if projects:
                    for proj in projects:
                        p_name = proj.get('name', 'Unnamed Project')
                        p_dur = proj.get('duration', '')
                        p_desc = proj.get('description', '')
                        
                        html_proj = "<div class='project-card'>"
                        html_proj += f"<div class='project-title'>{p_name}</div>"
                        if p_dur:
                            html_proj += f"<div class='project-duration'>📅 {p_dur}</div>"
                        html_proj += f"<div class='project-desc'>{p_desc}</div>"
                        html_proj += "</div>"
                        st.markdown(html_proj, unsafe_allow_html=True)
                else:
                    st.info("No projects detected.")
                st.markdown("</div>", unsafe_allow_html=True)
            
            # Download Buttons
            st.markdown("<br>", unsafe_allow_html=True)
            
            btn_col1, btn_col2 = st.columns(2)
            
            with btn_col1:
                pdf_bytes = create_pdf_summary(data)
                st.download_button(
                    label="⬇️ Download PDF Summary",
                    data=pdf_bytes,
                    file_name=f"CV_Summary_{name.replace(' ', '_')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                
            with btn_col2:
                json_string = json.dumps(data, indent=4)
                st.download_button(
                    label="⬇️ Download Raw JSON Data",
                    data=json_string,
                    file_name=f"CV_Data_{name.replace(' ', '_')}.json",
                    mime="application/json",
                    use_container_width=True
                )
