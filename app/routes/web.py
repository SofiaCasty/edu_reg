from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.auth import validate_email, validate_password_strength, verify_password
from app.database import get_db
from app.dependencies import current_user
from app.models import GradeRecord, RoleEnum, School, Student, StudentEnrollment, StudentTutorLink, Teacher, TeacherAssignment, User


router = APIRouter()
TERM_OPTIONS = ["Trimestre 1", "Trimestre 2", "Trimestre 3", "Trimestre 4"]


def render(request: Request, template_name: str, **context):
    templates = request.app.state.templates
    base_context = {"request": request, "current_user": context.get("current_user"), "RoleEnum": RoleEnum}
    base_context.update(context)
    return templates.TemplateResponse(template_name, base_context)


def render_standalone(request: Request, template_name: str, **context):
    templates = request.app.state.templates
    standalone_context = {"request": request, "RoleEnum": RoleEnum}
    standalone_context.update(context)
    return templates.TemplateResponse(template_name, standalone_context)


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
                "avg_score": round(avg_score or 0, 2),
                "max_score": round(max_score or 0, 2),
                "min_score": round(min_score or 0, 2),
                "total_scores": total_scores,
                "bar_width": max(8, min(100, int((avg_score or 0) * 10))),
                "terms": latest_map.get(subject, []),
            }
            for subject, avg_score, max_score, min_score, total_scores in subject_rows
        ]
        return render(
            request,
            "reports.html",
            current_user=user,
            student_view=True,
            student_name=student.full_name if student else user.full_name,
            student_obj=student,
            student_enrollment=enrollment,
            student_school=school,
            overall_avg=round(overall_avg, 2),
            total_subjects=total_subjects,
            best_subject=best_subject[0] if best_subject else "-",
            support_subject=support_subject[0] if support_subject else "-",
            student_report=student_report,
        )

    school_codes = visible_school_codes(user, db)
    teacher_rows_stmt = (
        select(School.name, func.count(func.distinct(TeacherAssignment.id_persona)))
        .join(TeacherAssignment, TeacherAssignment.school_code == School.code)
        .group_by(School.name)
        .order_by(School.name)
    )
    student_rows_stmt = (
        select(School.name, func.count(StudentEnrollment.id))
        .join(StudentEnrollment, StudentEnrollment.school_code == School.code)
        .group_by(School.name)
        .order_by(School.name)
    )
    grade_rows_stmt = (
        select(School.name, func.avg(GradeRecord.score))
        .join(StudentEnrollment, StudentEnrollment.school_code == School.code)
        .join(GradeRecord, GradeRecord.enrollment_id == StudentEnrollment.id)
        .group_by(School.name)
        .order_by(School.name)
    )
    if school_codes is not None:
        teacher_rows_stmt = teacher_rows_stmt.where(School.code.in_(school_codes))
        student_rows_stmt = student_rows_stmt.where(School.code.in_(school_codes))
        grade_rows_stmt = grade_rows_stmt.where(School.code.in_(school_codes))
    if user.role == RoleEnum.STUDENT and user.school_code:
        teacher_rows_stmt = teacher_rows_stmt.where(School.code == user.school_code)
        student_rows_stmt = student_rows_stmt.where(School.code == user.school_code)
        grade_rows_stmt = grade_rows_stmt.where(School.code == user.school_code)

    teacher_rows = db.execute(teacher_rows_stmt.limit(25)).all()
    student_rows = db.execute(student_rows_stmt.limit(25)).all()
    grade_rows = [(name, round(avg or 0, 2)) for name, avg in db.execute(grade_rows_stmt.limit(25)).all()]
    return render(
        request,
        "reports.html",
        current_user=user,
        teacher_rows=teacher_rows,
        student_rows=student_rows,
        grade_rows=grade_rows,
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
