from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.auth import validate_email, validate_password_strength, verify_password
from app.database import get_db
from app.dependencies import current_user
from app.models import GradeRecord, RoleEnum, School, Student, StudentEnrollment, StudentTutorLink, Teacher, TeacherAssignment, User


router = APIRouter()


def render(request: Request, template_name: str, **context):
    templates = request.app.state.templates
    base_context = {"request": request, "current_user": context.get("current_user"), "RoleEnum": RoleEnum}
    base_context.update(context)
    return templates.TemplateResponse(template_name, base_context)


def visible_school_codes(user: User, db: Session) -> list[str] | None:
    if user.role == RoleEnum.ADMIN:
        return None
    if user.role in {RoleEnum.PRINCIPAL, RoleEnum.TEACHER, RoleEnum.STUDENT, RoleEnum.STUDENT_TUTOR} and user.school_code:
        return [user.school_code]
    return []


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
def schools(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(School).order_by(School.name)
    school_codes = visible_school_codes(user, db)
    if school_codes is not None:
        stmt = stmt.where(School.code.in_(school_codes))
    schools_list = db.scalars(stmt.limit(200)).all()
    return render(request, "schools.html", current_user=user, schools=schools_list)


@router.get("/teachers", response_class=HTMLResponse)
def teachers(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = (
        select(Teacher, TeacherAssignment, School)
        .join(TeacherAssignment, TeacherAssignment.id_persona == Teacher.id_persona)
        .join(School, School.code == TeacherAssignment.school_code)
        .order_by(School.name, Teacher.last_names, Teacher.first_names)
    )
    school_codes = visible_school_codes(user, db)
    if school_codes is not None:
        stmt = stmt.where(TeacherAssignment.school_code.in_(school_codes))
    if user.role == RoleEnum.TEACHER and user.teacher:
        stmt = stmt.where(Teacher.id == user.teacher.id)
    rows = db.execute(stmt.limit(200)).all()
    return render(request, "teachers.html", current_user=user, rows=rows)


@router.get("/students", response_class=HTMLResponse)
def students(
    request: Request,
    q: str | None = None,
    grade_label: str | None = None,
    section_code: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Student, StudentEnrollment, School)
        .join(StudentEnrollment, StudentEnrollment.nie == Student.nie)
        .join(School, School.code == StudentEnrollment.school_code)
        .order_by(School.name, Student.last_name1, Student.first_name1)
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
    if user.role == RoleEnum.STUDENT and user.student:
        stmt = stmt.where(Student.id == user.student.id)
    if user.role == RoleEnum.STUDENT_TUTOR:
        student_ids = [link.student_id for link in user.tutor_links]
        stmt = stmt.where(Student.id.in_(student_ids))

    rows = db.execute(stmt.limit(250)).all()
    return render(
        request,
        "students.html",
        current_user=user,
        rows=rows,
        filters={"q": q or "", "grade_label": grade_label or "", "section_code": section_code or ""},
    )


@router.get("/grades", response_class=HTMLResponse)
def grades(
    request: Request,
    q: str | None = None,
    grade_label: str | None = None,
    section_code: str | None = None,
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
    if user.role == RoleEnum.STUDENT and user.student_id:
        stmt = stmt.where(Student.id == user.student_id)
    if user.role == RoleEnum.STUDENT_TUTOR:
        stmt = stmt.where(Student.id.in_([link.student_id for link in user.tutor_links]))
    rows = db.execute(stmt.limit(300)).all()
    return render(
        request,
        "grades.html",
        current_user=user,
        rows=rows,
        filters={"q": q or "", "grade_label": grade_label or "", "section_code": section_code or ""},
    )


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
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
