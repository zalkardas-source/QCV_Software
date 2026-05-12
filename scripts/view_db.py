import sqlite3

def view_database():
    print("\n" + "="*50)
    print("🗄️ DATENBANK-INHALT (backend/qcv_app.db)")
    print("="*50)
    
    conn = sqlite3.connect("backend/qcv_app.db")
    cursor = conn.cursor()
    
    # 1. Profile
    cursor.execute("SELECT id, name, location, email FROM cv_profiles")
    profiles = cursor.fetchall()
    print("\n📋 TABELLE: cv_profiles")
    if not profiles:
        print("  -> Leer (Bitte lade zuerst einen Lebenslauf im Browser hoch!)")
    else:
        for p in profiles:
            print(f"  [ID: {p[0]}] Name: {p[1]} | Ort: {p[2]} | Email: {p[3]}")
            
    # 2. Skills
    cursor.execute("SELECT id, cv_profile_id, name, rating FROM skills")
    skills = cursor.fetchall()
    print("\n🧠 TABELLE: skills")
    if not skills:
        print("  -> Leer")
    else:
        for s in skills:
            print(f"  [Skill-ID: {s[0]}] (Gehört zu CV-ID: {s[1]}) -> {s[2]} (Rating: {s[3]})")
            
    # 3. Projekte
    cursor.execute("SELECT id, cv_profile_id, name, duration FROM projects")
    projects = cursor.fetchall()
    print("\n🚀 TABELLE: projects")
    if not projects:
        print("  -> Leer")
    else:
        for p in projects:
            print(f"  [Project-ID: {p[0]}] (Gehört zu CV-ID: {p[1]}) -> {p[2]} (Dauer: {p[3]})")

    conn.close()
    print("\n" + "="*50 + "\n")

if __name__ == "__main__":
    view_database()
