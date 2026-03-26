from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.auth import hash_password, validate_email, validate_password_strength, verify_password
from app.database import get_db
from app.dependencies import current_user
from app.models import (
    GradeRecord, RoleEnum, School, SchoolSection, SchoolSubject,
    Student, StudentEnrollment, StudentTutorLink,
    Teacher, TeacherAssignment, TeacherSubject, User,
)


router = APIRouter()
TERM_OPTIONS = ["Trimestre 1", "Trimestre 2", "Trimestre 3", "Trimestre 4"]


def render(request: Request, template_name: str, **context):
    templates = request.app.state.templates
    base_context = {"request": request, "current_user": context.get("current_user"), "RoleEnum": RoleEnum}
    base_context.update(context)
    return templates.TemplateResponse(request, template_name, context=base_context)


def render_standalone(request: Request, template_name: str, **context):
    templates = request.app.state.templates
    standalone_context = {"request": request, "RoleEnum": RoleEnum}
    standalone_context.update(context)
    return templates.TemplateResponse(request, template_name, context=standalone_context)


def normalize_term_name(raw: str | None) -> str:
    value = (raw or "").strip()
    mapping = {
        "Semestre 1": "Trimestre 1",
        "Semestre 2": "Trimestre 2",
    }
    return mapping.get(value, value or "Trimestre 1")


def visible_school_codes(user: User, db: Session) -> list[str] | None:
    if user.role == RoleEnum.ADMIN:
        return None
    if user.role in {RoleEnum.PRINCIPAL, RoleEnum.TEACHER, RoleEnum.STUDENT, RoleEnum.STUDENT_TUTOR} and user.school_code:
        return [user.school_code]
    return []


def redirect_with_message(path: str, message: str, error: bool = False) -> RedirectResponse:
    query = urlencode({"message": message, "message_type": "error" if error else "success"})
    return RedirectResponse(f"{path}?{query}", status_code=303)


def teacher_assignment_scope(user: User, db: Session) -> list[TeacherAssignment]:
    if user.role != RoleEnum.TEACHER or not user.teacher:
        return []
    return db.scalars(
        select(TeacherAssignment).where(TeacherAssignment.id_persona == user.teacher.id_persona)
    ).all()


def teacher_grade_configs(user: User, db: Session) -> list[dict]:
    if user.role != RoleEnum.TEACHER or not user.teacher:
        return []
    configs = []
    seen = set()
    assignments = teacher_assignment_scope(user, db)
    for assignment in assignments:
        if not assignment.grade_label:
            continue
        subject_name = (assignment.component_type or "GRADO").strip() or "GRADO"
        if subject_name.upper() == "DIRECTOR":
            continue
        key = (assignment.school_code, assignment.grade_label, assignment.section_id or "", subject_name)
        if key in seen:
            continue
        seen.add(key)
        configs.append(
            {
                "school_code": assignment.school_code,
                "grade_label": assignment.grade_label,
                "section_code": assignment.section_id or "",
                "subject_name": subject_name,
                "label": f"{assignment.grade_label} | {subject_name} | Seccion {assignment.section_id or 'General'}",
            }
        )
    return configs


def teacher_can_manage_enrollment(user: User, enrollment: StudentEnrollment, db: Session) -> bool:
    if user.role == RoleEnum.ADMIN:
        return True
    if user.role != RoleEnum.TEACHER or not user.teacher:
        return False
    assignments = teacher_assignment_scope(user, db)
    for assignment in assignments:
        if assignment.school_code != enrollment.school_code:
            continue
        section_match = not assignment.section_id or assignment.section_id == enrollment.section_code
        grade_match = not assignment.grade_label or assignment.grade_label == enrollment.grade_label
        if section_match or grade_match:
            return True
    return False


def grade_form_options(user: User, db: Session) -> list[tuple[StudentEnrollment, Student, School]]:
    stmt = (
        select(StudentEnrollment, Student, School)
        .join(Student, Student.nie == StudentEnrollment.nie)
        .join(School, School.code == StudentEnrollment.school_code)
        .order_by(School.name, Student.last_name1, Student.first_name1)
    )
    if user.role == RoleEnum.ADMIN:
        return db.execute(stmt.limit(100)).all()
    if user.role == RoleEnum.TEACHER:
        assignments = teacher_assignment_scope(user, db)
        conditions = []
        for assignment in assignments:
            base = StudentEnrollment.school_code == assignment.school_code
            if assignment.section_id:
                conditions.append(base & (StudentEnrollment.section_code == assignment.section_id))
            elif assignment.grade_label:
                conditions.append(base & (StudentEnrollment.grade_label == assignment.grade_label))
            else:
                conditions.append(base)
        if not conditions:
            return []
        stmt = stmt.where(or_(*conditions))
        return db.execute(stmt.limit(200)).all()
    return []


def load_grade_record_or_404(grade_id: int, db: Session) -> GradeRecord:
    grade = db.scalar(
        select(GradeRecord)
        .options(joinedload(GradeRecord.enrollment), joinedload(GradeRecord.student))
        .where(GradeRecord.id == grade_id)
    )
    if not grade:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    return grade


def selected_teacher_config(user: User, db: Session, grade_label: str | None, subject_name: str | None, section_code: str | None):
    if user.role != RoleEnum.TEACHER:
        return None
    for config in teacher_grade_configs(user, db):
        if config["grade_label"] != (grade_label or ""):
            continue
        if config["subject_name"] != (subject_name or ""):
            continue
        if config["section_code"] != (section_code or ""):
            continue
        return config
    return None


def students_for_teacher_config(config: dict, db: Session):
    stmt = (
        select(StudentEnrollment, Student, School)
        .join(Student, Student.nie == StudentEnrollment.nie)
        .join(School, School.code == StudentEnrollment.school_code)
        .where(
            StudentEnrollment.school_code == config["school_code"],
            StudentEnrollment.grade_label == config["grade_label"],
        )
        .order_by(Student.last_name1, Student.first_name1)
    )
    if config["section_code"]:
        stmt = stmt.where(StudentEnrollment.section_code == config["section_code"])
    return db.execute(stmt.limit(250)).all()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    errors = []
    if not validate_email(email):
        errors.append("Ingrese un email valido.")
    user = db.scalar(select(User).where(User.email == email.lower()))
    if not user or not verify_password(password, user.password_hash):
        errors.append("Credenciales invalidas.")
    if errors:
        return render(request, "login.html", errors=errors, email=email)

    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    school_codes = visible_school_codes(user, db)
    schools_stmt = select(func.count(School.code))
    students_stmt = select(func.count(StudentEnrollment.id))
    teachers_stmt = select(func.count(TeacherAssignment.id))
    grades_stmt = select(func.avg(GradeRecord.score))

    if school_codes is not None:
        schools_stmt = schools_stmt.where(School.code.in_(school_codes))
        students_stmt = students_stmt.where(StudentEnrollment.school_code.in_(school_codes))
        teachers_stmt = teachers_stmt.where(TeacherAssignment.school_code.in_(school_codes))
        grades_stmt = grades_stmt.join(StudentEnrollment, GradeRecord.enrollment_id == StudentEnrollment.id).where(
            StudentEnrollment.school_code.in_(school_codes)
        )

    if user.role == RoleEnum.STUDENT and user.student_id:
        students_stmt = students_stmt.where(StudentEnrollment.nie == user.student.nie)
        grades_stmt = grades_stmt.where(GradeRecord.student_id == user.student_id)
    if user.role == RoleEnum.TEACHER and user.teacher:
        teachers_stmt = teachers_stmt.where(TeacherAssignment.id_persona == user.teacher.id_persona)
    if user.role == RoleEnum.STUDENT_TUTOR:
        student_ids = [link.student_id for link in user.tutor_links]
        students_stmt = students_stmt.where(StudentEnrollment.nie.in_(select(Student.nie).where(Student.id.in_(student_ids))))
        grades_stmt = grades_stmt.where(GradeRecord.student_id.in_(student_ids))

    metrics = {
        "schools": db.scalar(schools_stmt) or 0,
        "students": db.scalar(students_stmt) or 0,
        "teachers": db.scalar(teachers_stmt) or 0,
        "average_grade": round(db.scalar(grades_stmt) or 0, 2),
    }

    recent_stmt = (
        select(GradeRecord)
        .options(joinedload(GradeRecord.student), joinedload(GradeRecord.enrollment))
        .join(StudentEnrollment, GradeRecord.enrollment_id == StudentEnrollment.id)
        .order_by(GradeRecord.created_at.desc())
    )
    if school_codes is not None:
        recent_stmt = recent_stmt.where(StudentEnrollment.school_code.in_(school_codes))
    if user.role == RoleEnum.STUDENT and user.student_id:
        recent_stmt = recent_stmt.where(GradeRecord.student_id == user.student_id)
    if user.role == RoleEnum.STUDENT_TUTOR:
        recent_stmt = recent_stmt.where(GradeRecord.student_id.in_([link.student_id for link in user.tutor_links]))
    recent_grades = db.scalars(recent_stmt.limit(10)).all()
    return render(request, "dashboard.html", current_user=user, metrics=metrics, recent_grades=recent_grades)


@router.get("/schools", response_class=HTMLResponse)
def schools(
    request: Request,
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    stmt = select(School).order_by(School.name)
    school_codes = visible_school_codes(user, db)
    if school_codes is not None:
        stmt = stmt.where(School.code.in_(school_codes))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(School.code.ilike(like), School.name.ilike(like)))
    schools_list = db.scalars(stmt.limit(500)).all()
    return render(
        request,
        "schools.html",
        current_user=user,
        schools=schools_list,
        filters={"q": q or ""},
    )


@router.get("/teachers", response_class=HTMLResponse)
def teachers(
    request: Request,
    school_code: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    stmt = (
        select(Teacher, TeacherAssignment, School)
        .join(TeacherAssignment, TeacherAssignment.id_persona == Teacher.id_persona)
        .join(School, School.code == TeacherAssignment.school_code)
        .order_by(School.name, Teacher.last_names, Teacher.first_names)
    )
    school_codes = visible_school_codes(user, db)
    require_filter = False
    school_options_stmt = select(School).order_by(School.name)
    if school_codes is not None:
        stmt = stmt.where(TeacherAssignment.school_code.in_(school_codes))
        school_options_stmt = school_options_stmt.where(School.code.in_(school_codes))
    elif user.role == RoleEnum.ADMIN and not school_code:
        require_filter = True
    if school_code:
        stmt = stmt.where(TeacherAssignment.school_code == school_code)
    if user.role == RoleEnum.TEACHER and user.teacher:
        stmt = stmt.where(Teacher.id == user.teacher.id)
    rows = [] if require_filter else db.execute(stmt.limit(200)).all()
    school_options = db.scalars(school_options_stmt.limit(500)).all()
    return render(
        request,
        "teachers.html",
        current_user=user,
        rows=rows,
        filters={"school_code": school_code or ""},
        require_filter=require_filter,
        school_options=school_options,
    )


@router.get("/students", response_class=HTMLResponse)
def students(
    request: Request,
    q: str | None = None,
    grade_label: str | None = None,
    section_code: str | None = None,
    school_code: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER, RoleEnum.STUDENT_TUTOR, RoleEnum.STUDENT}:
        raise HTTPException(status_code=403, detail="No autorizado")
    stmt = (
        select(Student, StudentEnrollment, School)
        .join(StudentEnrollment, StudentEnrollment.nie == Student.nie)
        .join(School, School.code == StudentEnrollment.school_code)
        .order_by(School.name, Student.last_name1, Student.first_name1)
    )
    school_codes = visible_school_codes(user, db)
    require_filter = False
    school_options_stmt = select(School).order_by(School.name)
    if school_codes is not None:
        stmt = stmt.where(StudentEnrollment.school_code.in_(school_codes))
        school_options_stmt = school_options_stmt.where(School.code.in_(school_codes))
    elif user.role == RoleEnum.ADMIN and not school_code:
        require_filter = True
    if school_code:
        stmt = stmt.where(StudentEnrollment.school_code == school_code)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Student.first_name1.ilike(like),
                Student.first_name2.ilike(like),
                Student.last_name1.ilike(like),
                Student.last_name2.ilike(like),
                Student.nie.ilike(like),
            )
        )
    if grade_label:
        stmt = stmt.where(StudentEnrollment.grade_label == grade_label)
    if section_code:
        stmt = stmt.where(StudentEnrollment.section_code == section_code)
    if user.role == RoleEnum.STUDENT and user.student:
        stmt = stmt.where(Student.id == user.student.id)
    if user.role == RoleEnum.STUDENT_TUTOR:
        student_ids = [link.student_id for link in user.tutor_links]
        stmt = stmt.where(Student.id.in_(student_ids))

    rows = [] if require_filter else db.execute(stmt.limit(120)).all()
    school_options = db.scalars(school_options_stmt.limit(500)).all()
    options_scope_stmt = select(StudentEnrollment.grade_label, StudentEnrollment.section_code)
    if school_codes is not None:
        options_scope_stmt = options_scope_stmt.where(StudentEnrollment.school_code.in_(school_codes))
    if school_code:
        options_scope_stmt = options_scope_stmt.where(StudentEnrollment.school_code == school_code)
    if not require_filter or school_codes is not None:
        option_rows = db.execute(options_scope_stmt.distinct().limit(500)).all()
    else:
        option_rows = []
    grade_options = sorted({grade for grade, _ in option_rows if grade})
    section_options = sorted({section for _, section in option_rows if section})
    return render(
        request,
        "students.html",
        current_user=user,
        rows=rows,
        filters={
            "q": q or "",
            "grade_label": grade_label or "",
            "section_code": section_code or "",
            "school_code": school_code or "",
        },
        require_filter=require_filter,
        school_options=school_options,
        grade_options=grade_options,
        section_options=section_options,
    )


@router.get("/grades", response_class=HTMLResponse)
def grades(
    request: Request,
    q: str | None = None,
    grade_label: str | None = None,
    section_code: str | None = None,
    subject_name: str | None = None,
    term_name: str | None = None,
    edit_id: int | None = None,
    message: str | None = None,
    message_type: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(GradeRecord, Student, StudentEnrollment, School)
        .join(Student, Student.id == GradeRecord.student_id)
        .join(StudentEnrollment, StudentEnrollment.id == GradeRecord.enrollment_id)
        .join(School, School.code == StudentEnrollment.school_code)
        .order_by(School.name, Student.last_name1, GradeRecord.subject_name)
    )
    school_codes = visible_school_codes(user, db)
    if school_codes is not None:
        stmt = stmt.where(StudentEnrollment.school_code.in_(school_codes))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Student.first_name1.ilike(like),
                Student.first_name2.ilike(like),
                Student.last_name1.ilike(like),
                Student.last_name2.ilike(like),
                Student.nie.ilike(like),
            )
        )
    if grade_label:
        stmt = stmt.where(StudentEnrollment.grade_label == grade_label)
    if section_code:
        stmt = stmt.where(StudentEnrollment.section_code == section_code)
    if subject_name:
        stmt = stmt.where(GradeRecord.subject_name == subject_name)
    if term_name:
        stmt = stmt.where(GradeRecord.term_name == term_name)
    if user.role == RoleEnum.STUDENT and user.student_id:
        stmt = stmt.where(Student.id == user.student_id)
    if user.role == RoleEnum.STUDENT_TUTOR:
        stmt = stmt.where(Student.id.in_([link.student_id for link in user.tutor_links]))
    rows = db.execute(stmt.limit(300)).all()
    enrollment_options = grade_form_options(user, db)
    edit_grade = load_grade_record_or_404(edit_id, db) if edit_id else None
    can_manage_grades = user.role in {RoleEnum.ADMIN, RoleEnum.TEACHER}
    teacher_configs = teacher_grade_configs(user, db)
    active_teacher_config = selected_teacher_config(user, db, grade_label, subject_name, section_code)
    teacher_students = []
    existing_grade_map = {}
    active_term_name = normalize_term_name(term_name)
    if active_teacher_config:
        teacher_students = students_for_teacher_config(active_teacher_config, db)
        enrollment_ids = [enrollment.id for enrollment, _, _ in teacher_students]
        if enrollment_ids:
            existing_rows = db.scalars(
                select(GradeRecord).where(
                    GradeRecord.enrollment_id.in_(enrollment_ids),
                    GradeRecord.subject_name == active_teacher_config["subject_name"],
                    GradeRecord.term_name == active_term_name,
                )
            ).all()
            existing_grade_map = {row.enrollment_id: row for row in existing_rows}
    return render(
        request,
        "grades.html",
        current_user=user,
        rows=rows,
        filters={
            "q": q or "",
            "grade_label": grade_label or "",
            "section_code": section_code or "",
            "subject_name": subject_name or "",
            "term_name": active_term_name,
        },
        enrollment_options=enrollment_options,
        can_manage_grades=can_manage_grades,
        edit_grade=edit_grade,
        message=message,
        message_type=message_type or "success",
        teacher_configs=teacher_configs,
        active_teacher_config=active_teacher_config,
        teacher_students=teacher_students,
        existing_grade_map=existing_grade_map,
        term_options=TERM_OPTIONS,
    )


@router.post("/grades/bulk-save")
async def bulk_save_grades(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role != RoleEnum.TEACHER:
        raise HTTPException(status_code=403, detail="No autorizado")
    form = await request.form()
    grade_label = str(form.get("grade_label", "")).strip()
    section_code = str(form.get("section_code", "")).strip()
    subject_name = str(form.get("subject_name", "")).strip()
    term_name = normalize_term_name(str(form.get("term_name", "Trimestre 1")))
    config = selected_teacher_config(user, db, grade_label, subject_name, section_code)
    if not config:
        return redirect_with_message("/grades", "Seleccion de grupo o materia no valida.", error=True)

    saved = 0
    deleted = 0
    teacher_students = students_for_teacher_config(config, db)
    enrollment_map = {str(enrollment.id): (enrollment, student) for enrollment, student, _ in teacher_students}

    for enrollment_id, (enrollment, student) in enrollment_map.items():
        score_raw = str(form.get(f"score_{enrollment_id}", "")).strip()
        delete_requested = str(form.get(f"delete_{enrollment_id}", "")).lower() in {"on", "true", "1"}
        existing = db.scalar(
            select(GradeRecord).where(
                GradeRecord.enrollment_id == enrollment.id,
                GradeRecord.student_id == student.id,
                GradeRecord.subject_name == subject_name,
                GradeRecord.term_name == term_name,
            )
        )
        if delete_requested and existing:
            db.delete(existing)
            deleted += 1
            continue
        if not score_raw:
            continue
        try:
            score = float(score_raw)
        except ValueError:
            continue
        if existing:
            existing.score = score
            saved += 1
        else:
            db.add(
                GradeRecord(
                    student_id=student.id,
                    enrollment_id=enrollment.id,
                    subject_name=subject_name,
                    term_name=term_name,
                    score=score,
                    comments=None,
                    created_at=datetime.utcnow(),
                )
            )
            saved += 1
    db.commit()
    msg = f"Se guardaron {saved} notas"
    if deleted:
        msg += f" y se eliminaron {deleted}"
    params = urlencode(
        {
            "grade_label": grade_label,
            "section_code": section_code,
            "subject_name": subject_name,
            "term_name": term_name,
            "message": f"{msg}.",
        }
    )
    return RedirectResponse(f"/grades?{params}", status_code=303)


@router.post("/grades")
def create_grade(
    request: Request,
    enrollment_id: int = Form(...),
    subject_name: str = Form(...),
    term_name: str = Form(...),
    score: float = Form(...),
    comments: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    enrollment = db.get(StudentEnrollment, enrollment_id)
    if not enrollment:
        return redirect_with_message("/grades", "Matricula no encontrada.", error=True)
    if not teacher_can_manage_enrollment(user, enrollment, db):
        return redirect_with_message("/grades", "No puedes agregar notas para este alumno.", error=True)
    student = db.scalar(select(Student).where(Student.nie == enrollment.nie))
    if not student:
        return redirect_with_message("/grades", "Alumno no encontrado.", error=True)
    existing = db.scalar(
        select(GradeRecord).where(
            GradeRecord.student_id == student.id,
            GradeRecord.enrollment_id == enrollment.id,
            GradeRecord.subject_name == subject_name.strip(),
            GradeRecord.term_name == term_name.strip(),
        )
    )
    if existing:
        return redirect_with_message("/grades", "Ya existe una nota para esa materia y periodo.", error=True)
    db.add(
        GradeRecord(
            student_id=student.id,
            enrollment_id=enrollment.id,
            subject_name=subject_name.strip(),
            term_name=term_name.strip(),
            score=score,
            comments=comments.strip() or None,
            created_at=datetime.utcnow(),
        )
    )
    db.commit()
    return redirect_with_message("/grades", "Nota agregada correctamente.")


@router.post("/grades/{grade_id}/edit")
def update_grade(
    grade_id: int,
    subject_name: str = Form(...),
    term_name: str = Form(...),
    score: float = Form(...),
    comments: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    grade = load_grade_record_or_404(grade_id, db)
    if not teacher_can_manage_enrollment(user, grade.enrollment, db):
        return redirect_with_message("/grades", "No puedes editar esta nota.", error=True)
    duplicate = db.scalar(
        select(GradeRecord).where(
            GradeRecord.id != grade.id,
            GradeRecord.student_id == grade.student_id,
            GradeRecord.enrollment_id == grade.enrollment_id,
            GradeRecord.subject_name == subject_name.strip(),
            GradeRecord.term_name == term_name.strip(),
        )
    )
    if duplicate:
        return redirect_with_message(
            f"/grades?edit_id={grade.id}",
            "Ya existe otra nota con esa materia y periodo.",
            error=True,
        )
    grade.subject_name = subject_name.strip()
    grade.term_name = term_name.strip()
    grade.score = score
    grade.comments = comments.strip() or None
    db.commit()
    return redirect_with_message("/grades", "Nota actualizada correctamente.")


@router.post("/grades/{grade_id}/delete")
def delete_grade(
    grade_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    grade = load_grade_record_or_404(grade_id, db)
    if not teacher_can_manage_enrollment(user, grade.enrollment, db):
        return redirect_with_message("/grades", "No puedes eliminar esta nota.", error=True)
    db.delete(grade)
    db.commit()
    return redirect_with_message("/grades", "Nota eliminada correctamente.")


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # ── STUDENT view ──
    if user.role == RoleEnum.STUDENT and user.student_id:
        student = db.get(Student, user.student_id)
        enrollment = db.scalar(
            select(StudentEnrollment)
            .where(StudentEnrollment.nie == student.nie)
            .order_by(StudentEnrollment.academic_year.desc())
            .limit(1)
        ) if student else None
        school = db.get(School, enrollment.school_code) if enrollment else None
        subject_rows = db.execute(
            select(
                GradeRecord.subject_name,
                func.avg(GradeRecord.score),
                func.max(GradeRecord.score),
                func.min(GradeRecord.score),
                func.count(GradeRecord.id),
            )
            .where(GradeRecord.student_id == user.student_id)
            .group_by(GradeRecord.subject_name)
            .order_by(GradeRecord.subject_name)
        ).all()
        latest_rows = db.execute(
            select(GradeRecord.subject_name, GradeRecord.term_name, GradeRecord.score)
            .where(GradeRecord.student_id == user.student_id)
            .order_by(GradeRecord.subject_name, GradeRecord.term_name)
        ).all()
        latest_map: dict[str, list[tuple[str, float]]] = {}
        for subject, term, score in latest_rows:
            latest_map.setdefault(subject, []).append((term, score))
        overall_avg = db.scalar(select(func.avg(GradeRecord.score)).where(GradeRecord.student_id == user.student_id)) or 0
        total_subjects = len(subject_rows)
        best_subject = max(subject_rows, key=lambda row: row[1] or 0, default=None)
        support_subject = min(subject_rows, key=lambda row: row[1] or 0, default=None)
        student_report = [
            {
                "subject_name": subject,
                "avg_score": round(float(avg_score or 0), 2),
                "max_score": round(float(max_score or 0), 2),
                "min_score": round(float(min_score or 0), 2),
                "total_scores": total_scores,
                "bar_width": max(8, min(100, int(float(avg_score or 0) * 10))),
                "terms": latest_map.get(subject, []),
                "passed": float(avg_score or 0) >= 7.0,
            }
            for subject, avg_score, max_score, min_score, total_scores in subject_rows
        ]
        chart_labels = [r["subject_name"] for r in student_report]
        chart_values = [r["avg_score"] for r in student_report]
        chart_colors = ["#22c55e" if r["passed"] else "#eab308" for r in student_report]
        return render(
            request,
            "reports.html",
            current_user=user,
            student_view=True,
            student_name=student.full_name if student else user.full_name,
            student_obj=student,
            student_enrollment=enrollment,
            student_school=school,
            overall_avg=round(float(overall_avg), 2),
            total_subjects=total_subjects,
            best_subject=best_subject[0] if best_subject else "-",
            support_subject=support_subject[0] if support_subject else "-",
            student_report=student_report,
            chart_labels=chart_labels,
            chart_values=chart_values,
            chart_colors=chart_colors,
        )

    # ── TEACHER view ──
    if user.role == RoleEnum.TEACHER and user.teacher:
        assignments = teacher_assignment_scope(user, db)
        teacher_bar_data = []
        all_passed = 0
        all_failed = 0
        for assignment in assignments:
            subject_name = (assignment.component_type or "GRADO").strip()
            if subject_name.upper() == "DIRECTOR" or subject_name.upper() == "GRADO":
                continue
            stmt = (
                select(func.avg(GradeRecord.score), func.count(GradeRecord.id))
                .join(StudentEnrollment, GradeRecord.enrollment_id == StudentEnrollment.id)
                .where(
                    StudentEnrollment.school_code == assignment.school_code,
                    GradeRecord.subject_name == subject_name,
                )
            )
            if assignment.grade_label:
                stmt = stmt.where(StudentEnrollment.grade_label == assignment.grade_label)
            if assignment.section_id:
                stmt = stmt.where(StudentEnrollment.section_code == assignment.section_id)
            row = db.execute(stmt).first()
            avg_val = float(row[0] or 0) if row and row[0] else 0
            count_val = int(row[1]) if row and row[1] else 0
            label = f"{subject_name} | {assignment.grade_label or '-'} {assignment.section_id or ''}"
            teacher_bar_data.append({"label": label, "value": round(avg_val, 2)})

            if count_val > 0:
                passed_count = db.scalar(
                    select(func.count(GradeRecord.id))
                    .join(StudentEnrollment, GradeRecord.enrollment_id == StudentEnrollment.id)
                    .where(
                        StudentEnrollment.school_code == assignment.school_code,
                        GradeRecord.subject_name == subject_name,
                        GradeRecord.score >= 7,
                    )
                ) or 0
                all_passed += passed_count
                all_failed += (count_val - passed_count)

        return render(
            request,
            "reports.html",
            current_user=user,
            teacher_view=True,
            teacher_bar_labels=[d["label"] for d in teacher_bar_data],
            teacher_bar_values=[d["value"] for d in teacher_bar_data],
            teacher_passed=all_passed,
            teacher_failed=all_failed,
        )

    # ── PRINCIPAL / ADMIN view ──
    school_codes = visible_school_codes(user, db)

    # Pie: teachers per subject
    subj_stmt = (
        select(TeacherAssignment.component_type, func.count(func.distinct(TeacherAssignment.id_persona)))
        .where(TeacherAssignment.component_type.is_not(None))
    )
    if school_codes is not None:
        subj_stmt = subj_stmt.where(TeacherAssignment.school_code.in_(school_codes))
    subj_stmt = subj_stmt.group_by(TeacherAssignment.component_type).order_by(func.count(func.distinct(TeacherAssignment.id_persona)).desc()).limit(12)
    teacher_per_subject = db.execute(subj_stmt).all()

    # Bar: avg per grade/section
    avg_stmt = (
        select(
            StudentEnrollment.grade_label,
            StudentEnrollment.section_code,
            func.avg(GradeRecord.score),
        )
        .join(GradeRecord, GradeRecord.enrollment_id == StudentEnrollment.id)
    )
    if school_codes is not None:
        avg_stmt = avg_stmt.where(StudentEnrollment.school_code.in_(school_codes))
    avg_stmt = avg_stmt.group_by(StudentEnrollment.grade_label, StudentEnrollment.section_code).order_by(StudentEnrollment.grade_label).limit(30)
    avg_rows = db.execute(avg_stmt).all()

    pie_labels = [row[0] or "Sin materia" for row in teacher_per_subject]
    pie_values = [int(row[1]) for row in teacher_per_subject]
    bar_labels = [f"{row[0] or '-'} {row[1] or ''}" for row in avg_rows]
    bar_values = [round(float(row[2] or 0), 2) for row in avg_rows]

    return render(
        request,
        "reports.html",
        current_user=user,
        principal_view=True,
        pie_labels=pie_labels,
        pie_values=pie_values,
        bar_labels=bar_labels,
        bar_values=bar_values,
    )


@router.get("/reports/boleta", response_class=HTMLResponse)
def student_report_card(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != RoleEnum.STUDENT or not user.student_id:
        raise HTTPException(status_code=403, detail="No autorizado")
    student = db.get(Student, user.student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Alumno no encontrado")
    enrollment = db.scalar(
        select(StudentEnrollment)
        .where(StudentEnrollment.nie == student.nie)
        .order_by(StudentEnrollment.academic_year.desc())
        .limit(1)
    )
    if not enrollment:
        raise HTTPException(status_code=404, detail="Matricula no encontrada")
    school = db.get(School, enrollment.school_code)
    grade_rows = db.execute(
        select(GradeRecord.subject_name, GradeRecord.term_name, GradeRecord.score)
        .where(GradeRecord.student_id == student.id, GradeRecord.enrollment_id == enrollment.id)
        .order_by(GradeRecord.subject_name, GradeRecord.term_name)
    ).all()
    term_columns = TERM_OPTIONS
    subject_map: dict[str, dict[str, float | None]] = {}
    for subject, term, score in grade_rows:
        normalized_term = normalize_term_name(term)
        subject_map.setdefault(subject, {column: None for column in term_columns})
        if normalized_term in subject_map[subject]:
            subject_map[subject][normalized_term] = score
    boleta_rows = []
    for subject, term_scores in subject_map.items():
        score_values = [term_scores["Trimestre 1"], term_scores["Trimestre 2"], term_scores["Trimestre 3"], term_scores["Trimestre 4"]]
        final_score = round(sum(value if value is not None else 0 for value in score_values) / 4, 1)
        boleta_rows.append(
            {
                "subject_name": subject,
                "trimester_1": term_scores["Trimestre 1"],
                "trimester_2": term_scores["Trimestre 2"],
                "trimester_3": term_scores["Trimestre 3"],
                "trimester_4": term_scores["Trimestre 4"],
                "final_score": final_score,
                "result": "Aprobado" if final_score >= 7 else "Reprobado",
            }
        )
    return render_standalone(
        request,
        "report_card.html",
        current_user=user,
        student=student,
        enrollment=enrollment,
        school=school,
        boleta_rows=boleta_rows,
        generated_at=datetime.utcnow(),
    )


# ───────────────────────────────────────────────────────────────
#  SCHOOLS CRUD  (Admin only)
# ───────────────────────────────────────────────────────────────

@router.get("/schools/new", response_class=HTMLResponse)
def school_new_form(request: Request, user: User = Depends(current_user)):
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="No autorizado")
    return render(request, "school_form.html", current_user=user, school=None)


@router.post("/schools", response_class=HTMLResponse)
def school_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    sector: str = Form(""),
    zone: str = Form(""),
    department_code: str = Form(""),
    municipality_code: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="No autorizado")
    existing = db.get(School, code.strip())
    if existing:
        return redirect_with_message("/schools/new", "Ya existe una institución con ese código.", error=True)
    db.add(School(
        code=code.strip(),
        name=name.strip(),
        sector=sector.strip() or None,
        zone=zone.strip() or None,
        department_code=int(department_code) if department_code.strip() else None,
        municipality_code=int(municipality_code) if municipality_code.strip() else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    db.commit()
    return redirect_with_message("/schools", "Institución creada correctamente.")


@router.get("/schools/{code}/edit", response_class=HTMLResponse)
def school_edit_form(code: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="No autorizado")
    school = db.get(School, code)
    if not school:
        raise HTTPException(status_code=404, detail="Institución no encontrada")
    return render(request, "school_form.html", current_user=user, school=school)


@router.post("/schools/{code}/edit", response_class=HTMLResponse)
def school_update(
    code: str,
    request: Request,
    name: str = Form(...),
    sector: str = Form(""),
    zone: str = Form(""),
    department_code: str = Form(""),
    municipality_code: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403, detail="No autorizado")
    school = db.get(School, code)
    if not school:
        raise HTTPException(status_code=404, detail="Institución no encontrada")
    school.name = name.strip()
    school.sector = sector.strip() or None
    school.zone = zone.strip() or None
    school.department_code = int(department_code) if department_code.strip() else None
    school.municipality_code = int(municipality_code) if municipality_code.strip() else None
    school.updated_at = datetime.utcnow()
    db.commit()
    return redirect_with_message("/schools", "Institución actualizada correctamente.")


# ───────────────────────────────────────────────────────────────
#  TEACHERS CRUD  (Principal creates)
# ───────────────────────────────────────────────────────────────

def _teacher_form_context(user: User, db: Session, teacher=None, teacher_user=None, assigned_subjects=None):
    school_code = user.school_code
    sections = []
    subjects = []
    if school_code:
        sections = db.scalars(
            select(SchoolSection)
            .where(SchoolSection.school_code == school_code)
            .order_by(SchoolSection.grade_label, SchoolSection.section_name)
        ).all()
        subjects = db.scalars(
            select(SchoolSubject)
            .where(SchoolSubject.school_code == school_code)
            .order_by(SchoolSubject.subject_name)
        ).all()
    return {
        "teacher": teacher,
        "teacher_user": teacher_user,
        "school_code": school_code,
        "sections": sections,
        "subjects": subjects,
        "assigned_subjects": assigned_subjects or [],
    }


@router.get("/teachers/new", response_class=HTMLResponse)
def teacher_new_form(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL}:
        raise HTTPException(status_code=403, detail="No autorizado")
    ctx = _teacher_form_context(user, db)
    return render(request, "teacher_form.html", current_user=user, **ctx)


@router.post("/teachers", response_class=HTMLResponse)
async def teacher_create(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL}:
        raise HTTPException(status_code=403, detail="No autorizado")
    form = await request.form()
    id_persona = str(form.get("id_persona", "")).strip()
    first_names = str(form.get("first_names", "")).strip()
    last_names = str(form.get("last_names", "")).strip()
    gender = str(form.get("gender", "")).strip()
    dui = str(form.get("dui", "")).strip()
    specialty = str(form.get("specialty", "")).strip()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", "")).strip()
    homeroom_section = str(form.get("homeroom_section", "")).strip()
    school_code = user.school_code

    if not id_persona or not first_names or not last_names:
        return redirect_with_message("/teachers/new", "Campos obligatorios incompletos.", error=True)
    if email and not validate_email(email):
        return redirect_with_message("/teachers/new", "Email inválido.", error=True)
    if password and not validate_password_strength(password):
        return redirect_with_message("/teachers/new", "La contraseña debe tener al menos 8 caracteres, una mayúscula y un carácter especial.", error=True)

    existing_teacher = db.scalar(select(Teacher).where(Teacher.id_persona == id_persona))
    if existing_teacher:
        return redirect_with_message("/teachers/new", "Ya existe un maestro con esa identificación.", error=True)

    teacher = Teacher(
        id_persona=id_persona,
        first_names=first_names,
        last_names=last_names,
        gender=gender or None,
        dui=dui or None,
        specialty=specialty or None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(teacher)
    db.flush()

    if email and password:
        existing_user = db.scalar(select(User).where(User.email == email))
        if existing_user:
            return redirect_with_message("/teachers/new", "Ya existe un usuario con ese email.", error=True)
        db.add(User(
            email=email,
            password_hash=hash_password(password),
            role=RoleEnum.TEACHER,
            full_name=f"{first_names} {last_names}",
            school_code=school_code,
            teacher_id=teacher.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))

    if homeroom_section and school_code:
        section = db.scalar(select(SchoolSection).where(SchoolSection.id == int(homeroom_section)))
        if section:
            db.add(TeacherAssignment(
                id_persona=id_persona,
                school_code=school_code,
                academic_year=datetime.utcnow().year,
                component_type="Grado",
                grade_label=section.grade_label,
                section_id=section.section_id,
                section_name=section.section_name,
                shift=section.shift,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ))

    subject_keys = [k for k in form.keys() if k.startswith("subject_section_")]
    for key in subject_keys:
        val = str(form.get(key, "")).strip()
        if not val:
            continue
        parts = val.split("|", 2)
        if len(parts) != 3:
            continue
        subj_name, section_id_str, grade_lbl = parts
        section = db.scalar(select(SchoolSection).where(SchoolSection.id == int(section_id_str))) if section_id_str.isdigit() else None
        db.add(TeacherSubject(
            id_persona=id_persona,
            school_code=school_code,
            academic_year=datetime.utcnow().year,
            component_type=subj_name,
            grade_label=grade_lbl,
            section_id=section.section_id if section else None,
            section_name=section.section_name if section else None,
            shift=section.shift if section else None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))

    db.commit()
    return redirect_with_message("/teachers", "Maestro creado correctamente.")


@router.get("/teachers/{teacher_id}/edit", response_class=HTMLResponse)
def teacher_edit_form(teacher_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL}:
        raise HTTPException(status_code=403, detail="No autorizado")
    teacher = db.get(Teacher, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Maestro no encontrado")
    teacher_user = db.scalar(select(User).where(User.teacher_id == teacher.id))
    assigned = db.scalars(
        select(TeacherSubject).where(TeacherSubject.id_persona == teacher.id_persona)
    ).all()
    ctx = _teacher_form_context(user, db, teacher=teacher, teacher_user=teacher_user, assigned_subjects=assigned)
    return render(request, "teacher_form.html", current_user=user, **ctx)


@router.post("/teachers/{teacher_id}/edit", response_class=HTMLResponse)
async def teacher_update(
    teacher_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL}:
        raise HTTPException(status_code=403, detail="No autorizado")
    teacher = db.get(Teacher, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Maestro no encontrado")
    form = await request.form()
    teacher.first_names = str(form.get("first_names", "")).strip() or teacher.first_names
    teacher.last_names = str(form.get("last_names", "")).strip() or teacher.last_names
    teacher.gender = str(form.get("gender", "")).strip() or teacher.gender
    teacher.dui = str(form.get("dui", "")).strip() or teacher.dui
    teacher.specialty = str(form.get("specialty", "")).strip() or teacher.specialty
    teacher.updated_at = datetime.utcnow()

    teacher_user = db.scalar(select(User).where(User.teacher_id == teacher.id))
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", "")).strip()
    if teacher_user:
        if email:
            teacher_user.email = email
        teacher_user.full_name = f"{teacher.first_names} {teacher.last_names}"
        if password and validate_password_strength(password):
            teacher_user.password_hash = hash_password(password)
        teacher_user.updated_at = datetime.utcnow()

    db.commit()
    return redirect_with_message("/teachers", "Maestro actualizado correctamente.")


# ───────────────────────────────────────────────────────────────
#  STUDENTS CRUD  (Principal / Teacher creates)
# ───────────────────────────────────────────────────────────────

@router.get("/students/new", response_class=HTMLResponse)
def student_new_form(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    school_code = user.school_code
    sections = []
    if school_code:
        sections = db.scalars(
            select(SchoolSection)
            .where(SchoolSection.school_code == school_code)
            .order_by(SchoolSection.grade_label, SchoolSection.section_name)
        ).all()
    return render(request, "student_form.html", current_user=user, student=None, enrollment=None,
                  sections=sections, school_code=school_code)


@router.post("/students", response_class=HTMLResponse)
def student_create(
    request: Request,
    nie: str = Form(...),
    first_name1: str = Form(...),
    last_name1: str = Form(...),
    first_name2: str = Form(""),
    last_name2: str = Form(""),
    gender: str = Form(""),
    birth_date: str = Form(""),
    section_id: str = Form(""),
    father_full_name: str = Form(""),
    mother_full_name: str = Form(""),
    address_full: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    school_code = user.school_code
    existing = db.scalar(select(Student).where(Student.nie == nie.strip()))
    if existing:
        return redirect_with_message("/students/new", "Ya existe un alumno con ese NIE.", error=True)

    from datetime import date as dt_date
    bd = None
    if birth_date.strip():
        try:
            bd = dt_date.fromisoformat(birth_date.strip())
        except ValueError:
            pass

    student = Student(
        nie=nie.strip(),
        first_name1=first_name1.strip(),
        first_name2=first_name2.strip() or None,
        last_name1=last_name1.strip(),
        last_name2=last_name2.strip() or None,
        gender=gender.strip() or None,
        birth_date=bd,
        father_full_name=father_full_name.strip() or None,
        mother_full_name=mother_full_name.strip() or None,
        address_full=address_full.strip() or None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(student)
    db.flush()

    if section_id and school_code:
        section = db.scalar(select(SchoolSection).where(SchoolSection.id == int(section_id))) if section_id.isdigit() else None
        db.add(StudentEnrollment(
            nie=nie.strip(),
            school_code=school_code,
            academic_year=datetime.utcnow().year,
            section_code=section.section_id if section else None,
            grade_label=section.grade_label if section else None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
    db.commit()
    return redirect_with_message("/students", "Alumno creado correctamente.")


@router.get("/students/{student_id}/edit", response_class=HTMLResponse)
def student_edit_form(student_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Alumno no encontrado")
    enrollment = db.scalar(
        select(StudentEnrollment).where(StudentEnrollment.nie == student.nie)
        .order_by(StudentEnrollment.academic_year.desc()).limit(1)
    )
    school_code = user.school_code or (enrollment.school_code if enrollment else None)
    sections = []
    if school_code:
        sections = db.scalars(
            select(SchoolSection)
            .where(SchoolSection.school_code == school_code)
            .order_by(SchoolSection.grade_label, SchoolSection.section_name)
        ).all()
    return render(request, "student_form.html", current_user=user, student=student,
                  enrollment=enrollment, sections=sections, school_code=school_code)


@router.post("/students/{student_id}/edit", response_class=HTMLResponse)
def student_update(
    student_id: int,
    request: Request,
    first_name1: str = Form(...),
    last_name1: str = Form(...),
    first_name2: str = Form(""),
    last_name2: str = Form(""),
    gender: str = Form(""),
    birth_date: str = Form(""),
    father_full_name: str = Form(""),
    mother_full_name: str = Form(""),
    address_full: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role not in {RoleEnum.ADMIN, RoleEnum.PRINCIPAL, RoleEnum.TEACHER}:
        raise HTTPException(status_code=403, detail="No autorizado")
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Alumno no encontrado")

    from datetime import date as dt_date
    bd = None
    if birth_date.strip():
        try:
            bd = dt_date.fromisoformat(birth_date.strip())
        except ValueError:
            pass

    student.first_name1 = first_name1.strip()
    student.first_name2 = first_name2.strip() or None
    student.last_name1 = last_name1.strip()
    student.last_name2 = last_name2.strip() or None
    student.gender = gender.strip() or None
    student.birth_date = bd
    student.father_full_name = father_full_name.strip() or None
    student.mother_full_name = mother_full_name.strip() or None
    student.address_full = address_full.strip() or None
    student.updated_at = datetime.utcnow()
    db.commit()
    return redirect_with_message("/students", "Alumno actualizado correctamente.")


# ───────────────────────────────────────────────────────────────
#  API helpers for dynamic forms
# ───────────────────────────────────────────────────────────────

@router.get("/api/school/{school_code}/sections")
def api_school_sections(school_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sections = db.scalars(
        select(SchoolSection)
        .where(SchoolSection.school_code == school_code)
        .order_by(SchoolSection.grade_label, SchoolSection.section_name)
    ).all()
    return [{"id": s.id, "grade_label": s.grade_label, "section_name": s.section_name, "shift": s.shift} for s in sections]


@router.get("/api/school/{school_code}/subjects")
def api_school_subjects(school_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    subjects = db.scalars(
        select(SchoolSubject)
        .where(SchoolSubject.school_code == school_code)
        .order_by(SchoolSubject.subject_name)
    ).all()
    return [{"id": s.id, "subject_name": s.subject_name} for s in subjects]

