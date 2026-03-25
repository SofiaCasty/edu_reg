# Sistema de Gestion Escolar

Aplicacion SSR construida con FastAPI, SQLAlchemy, MySQL 8 y Jinja2 para administrar instituciones, maestros, alumnos, notas y reportes. La propuesta adapta la data entregada a una experiencia tipo Antigravity, con panel web server-rendered, autenticacion por sesiones y despliegue dockerizado.

## Funcionalidades

- Autenticacion por email con validacion de formato, contrasenas con hash `bcrypt` y sesiones.
- Roles `admin`, `principal`, `teacher`, `student` y `student_tutor`.
- Catalogo de instituciones, alumnos, maestros y notas.
- Reportes analiticos por institucion.
- Buscador dinamico de notas por alumno, grado y seccion.
- Importacion automatica desde el ZIP compartido sin descomprimir manualmente.

## Modelo de datos adaptado

Se reutiliza la estructura base entregada en `ddl_data.txt` para:

- `schools`
- `teachers`
- `teacher_assignments`
- `students`
- `student_enrollments`

Y se extiende con:

- `users` para autenticacion y roles
- `student_tutor_links` para vincular encargados con estudiantes
- `grade_records` para notas y consolidado academico

## Credenciales iniciales

- `admin@antigravity.school` / `Admin123!`
- `principal.999999999@antigravity.school` / `Director123!`
- `teacher.999999999@antigravity.school` / `Teacher123!`
- `student.5061541@antigravity.school` / `Student123!`

El importador tambien genera cuentas derivadas para directores, docentes, alumnos y algunos encargados.

## Requisitos

- Docker y Docker Compose
- El archivo `/Users/sofiaromero/Downloads/drive-download-20260317T201651Z-1-001.zip`

## Ejecucion local

Modo desarrollo:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Modo produccion local:

```bash
docker compose up --build
```

La app quedara en [http://localhost:8000](http://localhost:8000).

## Scripts utiles

Importar data manualmente:

```bash
docker compose exec web python scripts/import_data.py
```

## Variables de entorno principales

- `SECRET_KEY`: clave para sesiones.
- `DATABASE_URL`: conexion SQLAlchemy a MySQL.
- `IMPORT_ARCHIVE_PATH`: ruta del ZIP de datos.
- `APP_ENV`: `development` o `production`.
