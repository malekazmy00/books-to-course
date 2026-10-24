"""Owner-triggered restart of failed stages with immutable source inputs."""
import hashlib
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from temporalio.exceptions import WorkflowAlreadyStartedError
from b2c_orchestration.audio import GenerateMP3Input, GenerateMP3Workflow
from b2c_orchestration.indexing import IndexApprovedBookInput, IndexApprovedBookWorkflow
from b2c_orchestration.ingest import IngestPDFInput, IngestPDFWorkflow

STAGES = {
    'mp3': (GenerateMP3Input, GenerateMP3Workflow, 'audio', 'reuse_saved_tracks'),
    'index': (IndexApprovedBookInput, IndexApprovedBookWorkflow, 'index', 'reuse_committed_index'),
    'ingest': (IngestPDFInput, IngestPDFWorkflow, 'ingest', 'reuse_committed_stages'),
}
FAILED = {'FAILED', 'TIMED_OUT', 'CANCELED', 'TERMINATED'}

class ResumeStageRequest(BaseModel):
    book_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9._-]+$')

def workflow_resume_router(get_client, verify_owner, remember_progress):
    router = APIRouter()

    async def context(workflow_id):
        match = re.fullmatch(r'b2c-(mp3|index|ingest)-[0-9a-f]{64}', workflow_id)
        if not match:
            raise HTTPException(404, 'this stage does not support generic resume')
        model, workflow, stage, mode = STAGES[match.group(1)]
        client = await get_client()
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        status = desc.status.name
        return client, handle, model, workflow, stage, mode, status

    @router.get('/v1/workflows/{workflow_id}/resume-capability')
    async def capability(workflow_id: str):
        try:
            _, _, _, _, stage, mode, status = await context(workflow_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, 'could not inspect stage recovery') from exc
        return {'workflow_id': workflow_id, 'status': status, 'stage': stage,
                'resume_available': status in FAILED, 'resumption_mode': mode}

    @router.post('/v1/workflows/{workflow_id}/resume', status_code=202)
    async def resume(workflow_id: str, request: ResumeStageRequest):
        try:
            client, handle, model, workflow, stage, mode, status = await context(workflow_id)
            if status not in FAILED:
                raise HTTPException(409, 'only a failed terminal stage can be resumed')
            history = await handle.fetch_history()
            attrs = next((e.workflow_execution_started_event_attributes for e in history.events
                          if e.HasField('workflow_execution_started_event_attributes')), None)
            if attrs is None or attrs.workflow_type.name != workflow.__name__:
                raise HTTPException(409, 'original stage input cannot be validated')
            inputs = await client.data_converter.decode(attrs.input.payloads, [model])
            if len(inputs) != 1:
                raise HTTPException(409, 'original stage input is ambiguous')
            inp = inputs[0]
            course = inp.course_id
            await verify_owner(course, request.book_id, stage, workflow_id)
            prefix = workflow_id.rsplit('-', 1)[0] + '-'
            resumed_id = prefix + hashlib.sha256((workflow_id+'\0safe-resume-v1').encode()).hexdigest()
            try:
                await client.start_workflow(workflow.run, inp, id=resumed_id, task_queue=attrs.task_queue.name)
            except WorkflowAlreadyStartedError:
                pass
            await remember_progress(course, request.book_id, stage, resumed_id)
            return {'course_id': course, 'workflow_id': resumed_id, 'resumed_from': workflow_id,
                    'stage': stage, 'resumption_mode': mode}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, 'stage could not resume safely') from exc
    return router
