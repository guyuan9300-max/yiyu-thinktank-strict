from typing import Annotated, Any
from fastapi import Body, Depends, FastAPI, status
from ..repository import CloudRepository, SessionIdentity
from ..repositories.mobile_consult import MobileConsultRepository

def register_mobile_consult_routes(app: FastAPI, repository: CloudRepository, identity_dependency: Any) -> None:
    domain=MobileConsultRepository(repository); Identity=Annotated[SessionIdentity,Depends(identity_dependency)]
    @app.post('/api/v2/mobile-consult/runs',status_code=status.HTTP_202_ACCEPTED)
    def start(payload:Annotated[dict[str,Any],Body()],identity:Identity)->dict[str,Any]: return domain.start(identity,payload=payload)
    @app.get('/api/v2/mobile-consult/runs/{run_id}')
    def get(run_id:str,identity:Identity)->dict[str,Any]: return domain.get(identity,run_id=run_id)
    @app.get('/api/v2/mobile-consult/projects/{project_id}/favorites')
    def favorites(project_id:str,identity:Identity)->dict[str,Any]: return domain.list_favorites(identity,project_id=project_id)
    @app.post('/api/v2/mobile-consult/answers/{answer_id}/favorite',status_code=status.HTTP_201_CREATED)
    def favorite(answer_id:str,payload:Annotated[dict[str,Any],Body()],identity:Identity)->dict[str,Any]: return domain.favorite(identity,answer_id=answer_id,payload=payload)
    @app.delete('/api/v2/mobile-consult/favorites/{favorite_id}')
    def unfavorite(favorite_id:str,identity:Identity)->dict[str,Any]: return domain.unfavorite(identity,favorite_id=favorite_id)
