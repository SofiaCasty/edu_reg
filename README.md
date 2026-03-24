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
- `AUTO_BOOTSTRAP_DATA`: cuando es `false`, evita la importacion/siembra automatica al iniciar la app.

## Conexion a base de datos en la nube

Para trabajar con una base MySQL remota, crea un archivo `.env` local con una URL de conexion y desactiva la siembra automatica si la base ya existe o no quieres importar datos al arrancar.

Ejemplo:

```bash
APP_ENV=production
SECRET_KEY=change-this-secret
DATABASE_URL=mysql+pymysql://edu_user:TU_PASSWORD@35.222.28.57:3306/edu_reg
AUTO_BOOTSTRAP_DATA=false
IMPORT_ARCHIVE_PATH=/app/data/drive-download-20260317T201651Z-1-001.zip
```

Luego levanta la app normalmente:

```bash
docker compose up --build
```

Nota:

- Si la base en la nube no contiene el esquema del proyecto, `Base.metadata.create_all()` creara las tablas faltantes.
- Si la base ya tiene data productiva, deja `AUTO_BOOTSTRAP_DATA=false` para no intentar sembrar usuarios/notas demo.
- Si la contrasena tiene caracteres especiales como `$`, codificalos en la URL. Ejemplo: `$` se convierte en `%24`.

### Si Docker no llega directo a la base remota

En macOS puede pasar que tu host se conecte a MySQL remoto pero el contenedor Docker no. En ese caso usa el proxy local incluido en este repo:

```bash
python3 scripts/mysql_tcp_proxy.py
```

Y apunta `DATABASE_URL` a:

```bash
mysql+pymysql://USUARIO:CLAVE@host.docker.internal:3307/NOMBRE_BD
```

Ejemplo:

```bash
DATABASE_URL=mysql+pymysql://edu_user:goes-ia-apps%242026@host.docker.internal:3307/edu_reg
AUTO_BOOTSTRAP_DATA=false
```

## Despliegue en Google Cloud

### 1. Preparar proyecto

```bash
gcloud auth login
gcloud config set project TU_PROYECTO_GCP
gcloud services enable appengine.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com
```

### 2. Crear instancia MySQL en Cloud SQL

```bash
gcloud sql instances create school-mysql \
  --database-version=MYSQL_8_0 \
  --cpu=1 \
  --memory=3840MB \
  --region=us-central1

gcloud sql databases create school_management --instance=school-mysql

gcloud sql users create school_user \
  --instance=school-mysql \
  --password=school_pass
```

### 3. Construir imagen y subir a Artifact Registry

```bash
gcloud artifacts repositories create school-app --repository-format=docker --location=us-central1

gcloud builds submit --tag us-central1-docker.pkg.dev/TU_PROYECTO_GCP/school-app/web:latest
```

### 4. Configurar variables para App Engine Flexible

Actualiza `app.yaml` y define estas variables al desplegar:

- `SECRET_KEY`
- `DATABASE_URL` apuntando a Cloud SQL, por ejemplo:
  `mysql+pymysql://school_user:school_pass@/school_management?unix_socket=/cloudsql/TU_PROYECTO_GCP:us-central1:school-mysql`
- `IMPORT_ARCHIVE_PATH` si el ZIP sera montado o si migraras la carga a Cloud Storage

### 5. Desplegar

```bash
gcloud app deploy app.yaml
```

### 6. Carga inicial de datos en la nube

Opciones recomendadas:

- Subir el ZIP a Cloud Storage y adaptar `IMPORT_ARCHIVE_PATH` a un volumen/descarga previa durante build o startup.
- Ejecutar el importador desde un job temporal o una shell del contenedor:

```bash
python scripts/import_data.py
```

## Recomendaciones para el siguiente paso

- Agregar Alembic para migraciones versionadas.
- Externalizar secretos con Secret Manager.
- Mover la importacion grande a una tarea async o job administrativo.
- Sustituir las notas generadas por importacion real cuando se entregue ese origen.
