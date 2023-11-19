import base64
import json
import logging
import os
import shutil
from tempfile import NamedTemporaryFile
import traceback
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from langchain.document_loaders import (
    WebBaseLoader,
    SeleniumURLLoader,
    RecursiveUrlLoader
)
from bs4 import BeautifulSoup as Soup
from dotenv import load_dotenv
from app.auth import get_current_username, get_current_username_admin, get_current_username_project, get_current_username_user
from app.brain import Brain
from app.database import Database, dbc, get_db
from app.databasemodels import UserDatabase
import urllib.parse

from app.models import ChatResponse, EmbeddingModel, HardwareInfo, ProjectInfo, ProjectModel, ProjectModelUpdate, QuestionModel, ChatModel, QuestionResponse, URLIngestModel, User, UserCreate, UserUpdate
from app.tools import FindFileLoader, IndexDocuments, ExtractKeywordsForMetadata, loadEnvVars
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from modules.embeddings import EMBEDDINGS
from modules.llms import LLMS
from modules.loaders import LOADERS
import logging
import psutil
import GPUtil

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session


load_dotenv()
loadEnvVars()

logging.basicConfig(level=os.environ["LOG_LEVEL"])

app = FastAPI(
    title="RestAI",
    description="Modular REST API bootstrap on top of LangChain. Create embeddings associated with a project tenant and interact using a LLM. RAG as a service.",
    version="3.1.1",
    contact={
        "name": "Pedro Dias",
        "url": "https://github.com/apocas/restai",
        "email": "petermdias@gmail.com",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)

if "RESTAI_DEV" in os.environ:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

brain = Brain()


@app.get("/")
async def get(request: Request):
    return "RESTAI, so many 'A's and 'I's, so little time..."


@app.get("/info")
async def get_info(user: User = Depends(get_current_username)):
    return {
        "version": app.version, "embeddings": list(
            EMBEDDINGS.keys()), "llms": list(
            LLMS.keys()), "loaders": list(
                LOADERS.keys())}


@app.get("/users/{username}", response_model=User)
async def get_user(username: str, user: User = Depends(get_current_username_user), db: Session = Depends(get_db)):
    try:
        return User.model_validate(dbc.get_user_by_username(db, username))
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=404, detail='{"error": ' + str(e) + '}')


@app.get("/users", response_model=list[User])
def read_users(
        user: User = Depends(get_current_username_admin),
        db: Session = Depends(get_db)):
    users = dbc.get_users(db)
    return users


@app.post("/users", response_model=User)
def create_user(userc: UserCreate,
                user: User = Depends(get_current_username_admin),
                db: Session = Depends(get_db)):
    try:
        user = dbc.create_user(db,
                               userc.username,
                               userc.password,
                               userc.is_admin)
        return user
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500,
            detail='{"error": "failed to create user ' + userc.username + '"}')


@app.patch("/users/{username}", response_model=User)
def update_user(
        username: str,
        userc: UserUpdate,
        user: User = Depends(get_current_username_user),
        db: Session = Depends(get_db)):
    try:
        user = dbc.get_user_by_username(db, username)
        if user is None:
            raise Exception("User not found")

        if not user.is_admin and userc.is_admin is True:
            raise Exception("Insuficient permissions")

        dbc.update_user(db, user, userc)

        if userc.projects is not None:
            dbc.delete_userprojects(db, user)
            for project in userc.projects:
                project = dbc.add_userproject(db, user, project)
        return user
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.delete("/users/{username}")
def delete_user(username: str,
                user: User = Depends(get_current_username_admin),
                db: Session = Depends(get_db)):
    try:
        user = dbc.get_user_by_username(db, username)
        if user is None:
            raise Exception("User not found")
        dbc.delete_user(db, user)
        return {"deleted": username}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.get("/hardware", response_model=HardwareInfo)
def get_hardware_info(user: User = Depends(get_current_username)):
    try:
        cpu_load = psutil.cpu_percent()
        ram_usage = psutil.virtual_memory().percent

        gpu_load = None
        gpu_temp = None
        gpu_ram_usage = None

        GPUs = GPUtil.getGPUs()
        if len(GPUs) > 0:
            gpu = GPUs[0]
            gpu_load = getattr(gpu, 'load', None)
            gpu_temp = getattr(gpu, 'temperature', None)
            gpu_ram_usage = getattr(gpu, 'memoryUtil', None)

        cpu_load = int(cpu_load)
        if gpu_load is not None:
            gpu_load = int(gpu_load * 100)

        if gpu_ram_usage is not None:
            gpu_ram_usage = int(gpu_ram_usage * 100)

        return HardwareInfo(
            cpu_load=cpu_load,
            ram_usage=ram_usage,
            gpu_load=gpu_load,
            gpu_temp=gpu_temp,
            gpu_ram_usage=gpu_ram_usage,
        )
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=404, detail='{"error": ' + str(e) + '}')


@app.get("/projects", response_model=list[ProjectModel])
async def get_projects(request: Request, user: User = Depends(get_current_username), db: Session = Depends(get_db)):
    if user.is_admin:
        return dbc.get_projects(db)
    else:
        projects = []
        for project in user.projects:
            for p in dbc.get_projects(db):
                if project.name == p.name:
                    projects.append(p)
        return projects


@app.get("/projects/{projectName}", response_model=ProjectInfo)
async def get_project(projectName: str, user: User = Depends(get_current_username_project), db: Session = Depends(get_db)):
    try:
        project = brain.findProject(projectName, db)
        dbInfo = project.db.get()

        output = ProjectInfo(
            name=project.model.name,
            embeddings=project.model.embeddings,
            llm=project.model.llm,
            system=project.model.system,
            sandboxed=project.model.sandboxed)
        output.documents = len(dbInfo["documents"])
        output.metadatas = len(dbInfo["metadatas"])

        return output
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=404, detail='{"error": ' + str(e) + '}')


@app.delete("/projects/{projectName}")
async def delete_project(projectName: str, user: User = Depends(get_current_username_project), db: Session = Depends(get_db)):
    try:
        if brain.deleteProject(projectName, db):
            return {"project": projectName}
        else:
            raise HTTPException(
                status_code=404, detail='{"error": "Project not found"}')
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.patch("/projects/{projectName}")
async def edit_project(projectName: str, projectModelUpdate: ProjectModelUpdate, user: User = Depends(get_current_username_project), db: Session = Depends(get_db)):
    try:
        if brain.editProject(projectName, projectModelUpdate, db):
            return {"project": projectName}
        else:
            raise HTTPException(
                status_code=404, detail='{"error": "Project not found"}')
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.post("/projects")
async def create_project(projectModel: ProjectModel, user: User = Depends(get_current_username), db: Session = Depends(get_db)):
    try:
        proj = brain.findProject(projectModel.name, db)
        if proj is not None:
            raise HTTPException(
                status_code=403,
                detail='{"error": "Project already exists"}')

        brain.createProject(projectModel, db)
        dbc.add_userproject(db, user, projectModel.name)
        return {"project": projectModel.name}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.post("/projects/{projectName}/embeddings/reset")
def project_reset(
        projectName: str,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    try:
        project = brain.findProject(projectName, db)
        project.db._client.reset()
        brain.initializeEmbeddings(project)

        return {"project": project.model.name}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=404, detail='{"error": ' + str(e) + '}')


@app.post("/projects/{projectName}/embeddings/find")
def get_embedding(projectName: str, embedding: EmbeddingModel,
                  user: User = Depends(get_current_username_project),
                  db: Session = Depends(get_db)):
    project = brain.findProject(projectName, db)
    docs = None

    collection = project.db._client.get_collection("langchain")
    if embedding.source.startswith(('http://', 'https://')):
        docs = collection.get(where={'source': embedding.source})
    else:
        docs = collection.get(where={'source': os.path.join(
            os.environ["UPLOADS_PATH"], project.model.name, embedding.source)})

    if (len(docs['ids']) == 0):
        return {"ids": []}
    else:
        return docs


@app.delete("/projects/{projectName}/embeddings/{id}")
def delete_embedding(
        projectName: str,
        id: str,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    project = brain.findProject(projectName, db)

    collection = project.db._client.get_collection("langchain")
    ids = collection.get(ids=[id])['ids']
    if len(ids):
        collection.delete(ids)
    return {"deleted": len(ids)}


@app.post("/projects/{projectName}/embeddings/ingest/url")
def ingest_url(projectName: str, ingest: URLIngestModel,
               user: User = Depends(get_current_username_project),
               db: Session = Depends(get_db)):
    try:
        project = brain.findProject(projectName, db)

        collection = project.db._client.get_collection("langchain")
        docs = collection.get(where={'source': ingest.url})

        if (len(docs['ids']) > 0):
            raise Exception("URL already ingested. Delete first.")

        loader = loader = SeleniumURLLoader(urls=[ingest.url])

        documents = loader.load()

        documents = ExtractKeywordsForMetadata(documents)

        ids = IndexDocuments(brain, project, documents)
        project.db.persist()

        return {"url": ingest.url, "documents": len(ids)}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.post("/projects/{projectName}/embeddings/ingest/upload")
def ingest_file(
        projectName: str,
        file: UploadFile,
        options: str = Form("{}"),
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    try:
        logger = logging.getLogger("embeddings_ingest_upload")
        project = brain.findProject(projectName, db)

        dest = os.path.join(os.environ["UPLOADS_PATH"],
                            project.model.name, file.filename)
        logger.info("Ingesting upload for destination: {}".format(dest))

        with open(dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        _, ext = os.path.splitext(file.filename or '')
        logger.debug("Filename: {}".format(file.filename))
        logger.debug("ContentType: {}".format(file.content_type))
        logger.debug("Extension: {}".format(ext))

        loader = FindFileLoader(
            dest, ext, json.loads(
                urllib.parse.unquote(options)))
        documents = loader.load()

        documents = ExtractKeywordsForMetadata(documents)

        ids = IndexDocuments(brain, project, documents)
        logger.debug("Documents: {}".format(len(ids)))
        project.db.persist()
        logger.debug("Persisten project to DB")

        return {
            "filename": file.filename,
            "type": file.content_type,
            "documents": len(ids)}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.get('/projects/{projectName}/embeddings/urls')
def list_urls(projectName: str, user: User = Depends(
        get_current_username_project),
        db: Session = Depends(get_db)):
    project = brain.findProject(projectName, db)

    collection = project.db._client.get_collection("langchain")

    docs = collection.get(
        include=["metadatas"]
    )

    urls = []

    for metadata in docs["metadatas"]:
        if metadata["source"].startswith(
                ('http://', 'https://')) and metadata["source"] not in urls:
            urls.append(metadata["source"])

    return {'urls': urls}


@app.get('/projects/{projectName}/embeddings/files')
def list_files(
        projectName: str,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    project = brain.findProject(projectName, db)
    project_path = os.path.join(os.environ["UPLOADS_PATH"], project.model.name)

    if not os.path.exists(project_path):
        return {'error': f'Project {projectName} not found'}

    if not os.path.isdir(project_path):
        return {'error': f'{project_path} is not a directory'}

    files = [f for f in os.listdir(project_path) if os.path.isfile(
        os.path.join(project_path, f))]
    return {'files': files}


@app.delete('/projects/{projectName}/embeddings/url/{url}')
def delete_url(
        projectName: str,
        url: str,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    project = brain.findProject(projectName, db)

    collection = project.db._client.get_collection("langchain")
    ids = collection.get(
        where={'source': base64.b64decode(url).decode('utf-8')})['ids']
    if len(ids):
        collection.delete(ids)

    return {"deleted": len(ids)}


@app.delete('/projects/{projectName}/embeddings/files/{fileName}')
def delete_file(
        projectName: str,
        fileName: str,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    project = brain.findProject(projectName, db)

    collection = project.db._client.get_collection("langchain")
    ids = collection.get(
        where={
            'source': os.path.join(
                os.environ["UPLOADS_PATH"],
                project.model.name,
                base64.b64decode(fileName).decode('utf-8'))})['ids']
    if len(ids):
        collection.delete(ids)

    project_path = os.path.join(os.environ["UPLOADS_PATH"], project.model.name)

    file_path = os.path.join(
        project_path, base64.b64decode(fileName).decode('utf-8'))
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail="{'error': f'File {fileName} not found'}")
    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=404, detail="{'error': f'File {fileName} not found'}")

    os.remove(file_path)

    return {"deleted": len(ids)}


@app.post("/projects/{projectName}/question", response_model=QuestionResponse)
def question_project(
        projectName: str,
        input: QuestionModel,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    try:
        project = brain.findProject(projectName, db)
        if input.system or project.model.system:
            answer, docs = brain.questionContext(project, input)
            sources = [{"content": doc.page_content,
                       "keywords": doc.metadata["keywords"],
                        "source": doc.metadata["source"]} for doc in docs]
            return {
                "question": input.question,
                "answer": answer,
                "sources": sources,
                "type": "questioncontext"}
        else:
            answer, docs = brain.question(project, input)
            sources = [{"content": doc.page_content,
                       "keywords": doc.metadata["keywords"],
                        "source": doc.metadata["source"]} for doc in docs]
            return {
                "question": input.question,
                "answer": answer,
                "sources": sources,
                "type": "question"}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


@app.post("/projects/{projectName}/chat", response_model=ChatResponse)
def chat_project(
        projectName: str,
        input: ChatModel,
        user: User = Depends(get_current_username_project),
        db: Session = Depends(get_db)):
    try:
        project = brain.findProject(projectName, db)
        chat, output = brain.chat(project, input)

        sources = [{"content": doc.page_content, "keywords": doc.metadata["keywords"],
                    "source": doc.metadata["source"]} for doc in output["source_documents"]]

        return {
            "message": output["question"],
            "response": output["answer"].strip(),
            "id": chat.id,
            "sources": sources,
            "type": "chat"}
    except Exception as e:
        logging.error(e)
        traceback.print_tb(e.__traceback__)
        raise HTTPException(
            status_code=500, detail='{"error": ' + str(e) + '}')


try:
    app.mount("/admin/", StaticFiles(directory="frontend/html/",
              html=True), name="static_admin")
    app.mount(
        "/admin/static/js",
        StaticFiles(
            directory="frontend/html/static/js"),
        name="static_js")
    app.mount(
        "/admin/static/css",
        StaticFiles(
            directory="frontend/html/static/css"),
        name="static_css")
    app.mount(
        "/admin/static/media",
        StaticFiles(
            directory="frontend/html/static/media"),
        name="static_media")
except BaseException:
    print("Admin interface not available. Did you run 'make frontend'?")
