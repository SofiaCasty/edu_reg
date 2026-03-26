from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum as SqlEnum, Float, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RoleEnum(str, Enum):
    ADMIN = "admin"
    PRINCIPAL = "principal"
    TEACHER = "teacher"
    STUDENT = "student"
    STUDENT_TUTOR = "student_tutor"


class School(Base):
    __tablename__ = "schools"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sector: Mapped[str | None] = mapped_column(String(30))
    zone: Mapped[str | None] = mapped_column(String(30))
    department_code: Mapped[int | None] = mapped_column(Integer, index=True)
    municipality_code: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    teacher_assignments: Mapped[list[TeacherAssignment]] = relationship(back_populates="school")
    enrollments: Mapped[list[StudentEnrollment]] = relationship(back_populates="school")
    users: Mapped[list[User]] = relationship(back_populates="school")


class SchoolSection(Base):
    __tablename__ = "school_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    school_code: Mapped[str] = mapped_column(String(20), ForeignKey("schools.code"), index=True)
    grade_label: Mapped[str | None] = mapped_column(String(60))
    section_id: Mapped[str | None] = mapped_column(String(30))
    section_name: Mapped[str | None] = mapped_column(String(30))
    shift: Mapped[str | None] = mapped_column(String(30))
    academic_year: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class SchoolSubject(Base):
    __tablename__ = "school_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    school_code: Mapped[str] = mapped_column(String(20), ForeignKey("schools.code"), index=True)
    subject_name: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class TeacherSubject(Base):
    __tablename__ = "teacher_subjects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id_persona: Mapped[str] = mapped_column(String(30), index=True)
    school_code: Mapped[str] = mapped_column(String(20), index=True)
    academic_year: Mapped[int] = mapped_column(SmallInteger)
    component_type: Mapped[str | None] = mapped_column(String(512))
    grade_label: Mapped[str | None] = mapped_column(String(60))
    section_id: Mapped[str | None] = mapped_column(String(30))
    section_name: Mapped[str | None] = mapped_column(String(30))
    shift: Mapped[str | None] = mapped_column(String(30))
    cod_adscrito: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_persona: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    nip: Mapped[str | None] = mapped_column(String(30), index=True)
    dui: Mapped[str | None] = mapped_column(String(30), index=True)
    first_names: Mapped[str | None] = mapped_column(String(180))
    last_names: Mapped[str | None] = mapped_column(String(180))
    gender: Mapped[str | None] = mapped_column(String(15))
    specialty: Mapped[str | None] = mapped_column(String(180))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    assignments: Mapped[list[TeacherAssignment]] = relationship(back_populates="teacher")
    user: Mapped[User | None] = relationship(back_populates="teacher", uselist=False)

    @property
    def full_name(self) -> str:
        return " ".join(filter(None, [self.first_names, self.last_names])).strip()


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nie: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    gender: Mapped[str | None] = mapped_column(String(15))
    first_name1: Mapped[str | None] = mapped_column(String(80))
    first_name2: Mapped[str | None] = mapped_column(String(80))
    first_name3: Mapped[str | None] = mapped_column(String(80))
    last_name1: Mapped[str | None] = mapped_column(String(80))
    last_name2: Mapped[str | None] = mapped_column(String(80))
    last_name3: Mapped[str | None] = mapped_column(String(80))
    birth_date: Mapped[date | None] = mapped_column(Date)
    age_current: Mapped[int | None] = mapped_column(Integer)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    father_full_name: Mapped[str | None] = mapped_column(String(255))
    mother_full_name: Mapped[str | None] = mapped_column(String(255))
    address_full: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    enrollments: Mapped[list[StudentEnrollment]] = relationship(back_populates="student")
    grade_records: Mapped[list[GradeRecord]] = relationship(back_populates="student")
    user: Mapped[User | None] = relationship(back_populates="student", uselist=False)
    tutor_links: Mapped[list[StudentTutorLink]] = relationship(back_populates="student")

    @property
    def full_name(self) -> str:
        names = [
            self.first_name1,
            self.first_name2,
            self.first_name3,
            self.last_name1,
            self.last_name2,
            self.last_name3,
        ]
        return " ".join(filter(None, names)).strip()


class TeacherAssignment(Base):
    __tablename__ = "teacher_assignments"
    __table_args__ = (
        UniqueConstraint("id_persona", "school_code", "academic_year", "section_id", name="uq_teacher_assignment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_persona: Mapped[str] = mapped_column(ForeignKey("teachers.id_persona"), index=True)
    school_code: Mapped[str] = mapped_column(ForeignKey("schools.code"), index=True)
    academic_year: Mapped[int] = mapped_column(Integer, index=True)
    component_type: Mapped[str | None] = mapped_column(String(512))
    grade_label: Mapped[str | None] = mapped_column(String(60), index=True)
    section_id: Mapped[str | None] = mapped_column(String(30), index=True)
    section_name: Mapped[str | None] = mapped_column(String(30))
    shift: Mapped[str | None] = mapped_column(String(30))
    cod_adscrito: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    teacher: Mapped[Teacher] = relationship(back_populates="assignments")
    school: Mapped[School] = relationship(back_populates="teacher_assignments")


class StudentEnrollment(Base):
    __tablename__ = "student_enrollments"
    __table_args__ = (UniqueConstraint("nie", "school_code", "academic_year", name="uq_student_enrollment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nie: Mapped[str] = mapped_column(ForeignKey("students.nie"), index=True)
    school_code: Mapped[str] = mapped_column(ForeignKey("schools.code"), index=True)
    academic_year: Mapped[int] = mapped_column(Integer, index=True)
    section_code: Mapped[str | None] = mapped_column(String(30), index=True)
    grade_label: Mapped[str | None] = mapped_column(String(60), index=True)
    modality: Mapped[str | None] = mapped_column(String(80))
    submodality: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    student: Mapped[Student] = relationship(back_populates="enrollments")
    school: Mapped[School] = relationship(back_populates="enrollments")
    grade_records: Mapped[list[GradeRecord]] = relationship(back_populates="enrollment")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[RoleEnum] = mapped_column(SqlEnum(RoleEnum), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    school_code: Mapped[str | None] = mapped_column(ForeignKey("schools.code"), index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    school: Mapped[School | None] = relationship(back_populates="users")
    teacher: Mapped[Teacher | None] = relationship(back_populates="user")
    student: Mapped[Student | None] = relationship(back_populates="user")
    tutor_links: Mapped[list[StudentTutorLink]] = relationship(back_populates="tutor")


class StudentTutorLink(Base):
    __tablename__ = "student_tutor_links"
    __table_args__ = (UniqueConstraint("student_id", "tutor_user_id", name="uq_student_tutor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    tutor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    relationship_label: Mapped[str] = mapped_column(String(50), default="Encargado")

    student: Mapped[Student] = relationship(back_populates="tutor_links")
    tutor: Mapped[User] = relationship(back_populates="tutor_links")


class GradeRecord(Base):
    __tablename__ = "grade_records"
    __table_args__ = (
        UniqueConstraint("student_id", "enrollment_id", "subject_name", "term_name", name="uq_grade_record"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("student_enrollments.id"), index=True)
    subject_name: Mapped[str] = mapped_column(String(120), index=True)
    term_name: Mapped[str] = mapped_column(String(50), default="Trimestre 1")
    score: Mapped[float] = mapped_column(Float)
    comments: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    student: Mapped[Student] = relationship(back_populates="grade_records")
    enrollment: Mapped[StudentEnrollment] = relationship(back_populates="grade_records")

