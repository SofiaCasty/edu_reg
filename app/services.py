from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.models import (
    GradeRecord,
    RoleEnum,
    School,
    Student,
    StudentEnrollment,
    StudentTutorLink,
    Teacher,
    TeacherAssignment,
    User,
)


SUBJECTS = ["Matematicas", "Lenguaje", "Ciencias", "Sociales"]


@dataclass
class ImportSummary:
    schools: int = 0
    teachers: int = 0
    teacher_assignments: int = 0
    students: int = 0
    student_enrollments: int = 0
    users: int = 0
    tutor_links: int = 0
    grades: int = 0


def parse_datetime(raw: str | None) -> datetime | None:
    if not raw or raw == "NULL":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_date(raw: str | None):
    dt = parse_datetime(raw)
    return dt.date() if dt else None


def clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value in {"", "NULL"} else value


def read_zip_csv(archive_path: str | Path, filename: str):
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(filename) as handle:
            decoded = io.TextIOWrapper(handle, encoding="utf-8")
            reader = csv.DictReader(decoded, delimiter=";")
            for row in reader:
                yield {key: clean_value(value) for key, value in row.items()}


def bulk_insert_from_rows(db: Session, model, rows, mapper, chunk_size: int = 5000) -> int:
    total = 0
    batch = []
    for row in rows:
        batch.append(mapper(row))
        if len(batch) >= chunk_size:
            db.bulk_insert_mappings(model, batch)
            db.commit()
            total += len(batch)
            batch.clear()
    if batch:
        db.bulk_insert_mappings(model, batch)
        db.commit()
        total += len(batch)
    return total


def bootstrap_database(db: Session, archive_path: str | None = None) -> ImportSummary:
    settings = get_settings()
    archive = Path(archive_path or settings.import_archive_path)
    if not archive.exists():
        print(f"Advertencia: no se encontro el archivo de importacion: {archive}")
        if settings.auto_bootstrap_data:
            return summary
        else:
            return summary

    summary = ImportSummary()
    ensure_admin_user(db)

    data_already_loaded = bool(db.scalar(select(School.code).limit(1)))

    if not data_already_loaded:
        summary.schools = bulk_insert_from_rows(
            db,
            School,
            read_zip_csv(archive, "school_db_05022026.csv"),
            lambda row: {
                "code": row["code"],
                "name": row["name"],
                "sector": row["sector"],
                "zone": row["zone"],
                "department_code": int(row["department_code"]) if row["department_code"] else None,
                "municipality_code": int(row["municipality_code"]) if row["municipality_code"] else None,
                "created_at": parse_datetime(row["created_at"]),
                "updated_at": parse_datetime(row["updated_at"]),
            },
        )

        summary.teachers = bulk_insert_from_rows(
            db,
            Teacher,
            read_zip_csv(archive, "teacher_db_05022026.csv"),
            lambda row: {
                "id": int(row["id"]),
                "id_persona": row["id_persona"],
                "nip": row["nip"],
                "dui": row["dui"],
                "first_names": row["first_names"],
                "last_names": row["last_names"],
                "gender": row["gender"],
                "specialty": row["specialty"],
                "created_at": parse_datetime(row["created_at"]),
                "updated_at": parse_datetime(row["updated_at"]),
            },
        )

        summary.teacher_assignments = bulk_insert_from_rows(
            db,
            TeacherAssignment,
            read_zip_csv(archive, "teacher_assignments_db_05022026.csv"),
            lambda row: {
                "id": int(row["id"]),
                "id_persona": row["id_persona"],
                "school_code": row["school_code"],
                "academic_year": int(row["academic_year"]),
                "component_type": row["component_type"],
                "grade_label": row["grade_label"],
                "section_id": row["section_id"],
                "section_name": row["section_name"],
                "shift": row["shift"],
                "cod_adscrito": row["cod_adscrito"],
                "created_at": parse_datetime(row["created_at"]),
                "updated_at": parse_datetime(row["updated_at"]),
            },
        )

        summary.students = bulk_insert_from_rows(
            db,
            Student,
            read_zip_csv(archive, "estudent_db_05022026.csv"),
            lambda row: {
                "id": int(row["id"]),
                "nie": row["nie"],
                "gender": row["gender"],
                "first_name1": row["first_name1"],
                "first_name2": row["first_name2"],
                "first_name3": row["first_name3"],
                "last_name1": row["last_name1"],
                "last_name2": row["last_name2"],
                "last_name3": row["last_name3"],
                "birth_date": parse_date(row["birth_date"]),
                "age_current": int(row["age_current"]) if row["age_current"] else None,
                "is_manual": row["is_manual"] == "1",
                "father_full_name": row["father_full_name"],
                "mother_full_name": row["mother_full_name"],
                "address_full": row["address_full"],
                "created_at": parse_datetime(row["created_at"]),
                "updated_at": parse_datetime(row["updated_at"]),
            },
            chunk_size=10000,
        )

        summary.student_enrollments = bulk_insert_from_rows(
            db,
            StudentEnrollment,
            read_zip_csv(archive, "estudent_enrollments_db_05022026.csv"),
            lambda row: {
                "id": int(row["id"]),
                "nie": row["nie"],
                "school_code": row["school_code"],
                "academic_year": int(row["academic_year"]),
                "section_code": row["section_code"],
                "grade_label": row["grade_label"],
                "modality": row["modality"],
                "submodality": row["submodality"],
                "created_at": parse_datetime(row["created_at"]),
                "updated_at": parse_datetime(row["updated_at"]),
            },
            chunk_size=10000,
        )

    summary.users += ensure_role_users(db)
    summary.tutor_links += ensure_tutor_users(db)
    summary.grades += ensure_grade_records(db)
    db.commit()
    return summary


def ensure_admin_user(db: Session) -> None:
    settings = get_settings()
    admin = db.scalar(select(User).where(User.email == settings.default_admin_email))
    if admin:
        return
    db.add(
        User(
            email=settings.default_admin_email,
            password_hash=hash_password(settings.default_admin_password),
            role=RoleEnum.ADMIN,
            full_name="Administrador General",
        )
    )
    db.commit()


def sanitize_email_seed(raw: str) -> str:
    return "".join(char.lower() for char in raw if char.isalnum())


def ensure_role_users(db: Session) -> int:
    created = 0
    principals = db.scalars(
        select(TeacherAssignment).where(TeacherAssignment.component_type.ilike("%DIRECTOR%"))
    ).all()
    seen_principals: set[str] = set()
    for assignment in principals:
        if assignment.id_persona in seen_principals:
            continue
        seen_principals.add(assignment.id_persona)
        teacher = db.scalar(select(Teacher).where(Teacher.id_persona == assignment.id_persona))
        if not teacher:
            continue
        email = f"principal.{sanitize_email_seed(teacher.id_persona)}@antigravity.school"
        if not db.scalar(select(User).where(User.email == email)):
            db.add(
                User(
                    email=email,
                    password_hash=hash_password("Director123!"),
                    role=RoleEnum.PRINCIPAL,
                    full_name=teacher.full_name or "Director",
                    school_code=assignment.school_code,
                    teacher_id=teacher.id,
                )
            )
            created += 1

    teacher_candidates = db.scalars(select(Teacher).limit(75)).all()
    for teacher in teacher_candidates:
        email = f"teacher.{sanitize_email_seed(teacher.id_persona)}@antigravity.school"
        if not db.scalar(select(User).where(User.email == email)):
            school_code = db.scalar(
                select(TeacherAssignment.school_code)
                .where(TeacherAssignment.id_persona == teacher.id_persona)
                .limit(1)
            )
            db.add(
                User(
                    email=email,
                    password_hash=hash_password("Teacher123!"),
                    role=RoleEnum.TEACHER,
                    full_name=teacher.full_name or "Docente",
                    school_code=school_code,
                    teacher_id=teacher.id,
                )
            )
            created += 1

    student_candidates = db.scalars(select(Student).limit(150)).all()
    for student in student_candidates:
        email = f"student.{sanitize_email_seed(student.nie)}@antigravity.school"
        if not db.scalar(select(User).where(User.email == email)):
            school_code = db.scalar(
                select(StudentEnrollment.school_code).where(StudentEnrollment.nie == student.nie).limit(1)
            )
            db.add(
                User(
                    email=email,
                    password_hash=hash_password("Student123!"),
                    role=RoleEnum.STUDENT,
                    full_name=student.full_name or f"Alumno {student.nie}",
                    school_code=school_code,
                    student_id=student.id,
                )
            )
            created += 1
    db.commit()
    created += ensure_demo_teacher_user(db)
    return created


def ensure_demo_teacher_user(db: Session) -> int:
    demo_email = "teacher.demo@antigravity.school"
    existing = db.scalar(select(User).where(User.email == demo_email))
    if existing:
        return 0

    preferred_teacher = db.scalar(select(Teacher).where(Teacher.id_persona == "07200156"))
    if preferred_teacher:
        preferred_assignment = db.scalar(
            select(TeacherAssignment).where(TeacherAssignment.id_persona == preferred_teacher.id_persona).limit(1)
        )
        if preferred_assignment:
            db.add(
                User(
                    email=demo_email,
                    password_hash=hash_password("Teacher123!"),
                    role=RoleEnum.TEACHER,
                    full_name=preferred_teacher.full_name or "Docente de prueba",
                    school_code=preferred_assignment.school_code,
                    teacher_id=preferred_teacher.id,
                )
            )
            db.commit()
            return 1

    assignment = db.scalar(
        select(TeacherAssignment)
        .where(~TeacherAssignment.component_type.ilike("%DIRECTOR%"))
        .order_by(TeacherAssignment.id.asc())
        .limit(1)
    )
    if not assignment:
        return 0

    teacher = db.scalar(select(Teacher).where(Teacher.id_persona == assignment.id_persona))
    if not teacher:
        return 0

    db.add(
        User(
            email=demo_email,
            password_hash=hash_password("Teacher123!"),
            role=RoleEnum.TEACHER,
            full_name=teacher.full_name or "Docente de prueba",
            school_code=assignment.school_code,
            teacher_id=teacher.id,
        )
    )
    db.commit()
    return 1


def ensure_tutor_users(db: Session) -> int:
    created = 0
    students = db.scalars(select(Student).where((Student.father_full_name.is_not(None)) | (Student.mother_full_name.is_not(None))).limit(100)).all()
    for student in students:
        tutor_name = student.mother_full_name or student.father_full_name
        if not tutor_name:
            continue
        email = f"tutor.{sanitize_email_seed(student.nie)}@antigravity.school"
        tutor = db.scalar(select(User).where(User.email == email))
        if not tutor:
            school_code = db.scalar(
                select(StudentEnrollment.school_code).where(StudentEnrollment.nie == student.nie).limit(1)
            )
            tutor = User(
                email=email,
                password_hash=hash_password("Tutor123!"),
                role=RoleEnum.STUDENT_TUTOR,
                full_name=tutor_name,
                school_code=school_code,
            )
            db.add(tutor)
            db.flush()
            created += 1

        if not db.scalar(
            select(StudentTutorLink).where(
                StudentTutorLink.student_id == student.id, StudentTutorLink.tutor_user_id == tutor.id
            )
        ):
            db.add(
                StudentTutorLink(
                    student_id=student.id,
                    tutor_user_id=tutor.id,
                    relationship_label="Madre" if student.mother_full_name else "Padre",
                )
            )
    db.commit()
    return created


def ensure_grade_records(db: Session) -> int:
    if db.scalar(select(GradeRecord.id).limit(1)):
        return 0
    created = 0
    enrollments = db.scalars(select(StudentEnrollment).limit(500)).all()
    for enrollment in enrollments:
        student = db.scalar(select(Student).where(Student.nie == enrollment.nie))
        if not student:
            continue
        seed = int(student.nie[-2:]) if student.nie[-2:].isdigit() else 50
        for index, subject in enumerate(SUBJECTS):
            score = min(10.0, round(6.5 + ((seed + index * 3) % 35) / 10, 1))
            db.add(
                GradeRecord(
                    student_id=student.id,
                    enrollment_id=enrollment.id,
                    subject_name=subject,
                    term_name="Trimestre 1",
                    score=score,
                    comments="Calificacion generada a partir de la data importada.",
                )
            )
            created += 1
    db.commit()
    return created
