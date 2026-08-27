from typing import Annotated, Any
from fastapi import Body, Depends, FastAPI, Header
from ..repository import CloudRepository, SessionIdentity
from ..repositories.mobile_devices import MobileDeviceRepository

def register_mobile_device_routes(app:FastAPI,repository:CloudRepository,identity_dependency:Any)->None:
 domain=MobileDeviceRepository(repository);Identity=Annotated[SessionIdentity,Depends(identity_dependency)];Idem=Annotated[str,Header(alias='Idempotency-Key')]
 @app.post('/api/v2/mobile-devices/push-registration')
 def register(payload:Annotated[dict[str,Any],Body()],identity:Identity,idempotency_key:Idem)->dict[str,Any]:return domain.register(identity,payload=payload,idempotency_key=idempotency_key)
