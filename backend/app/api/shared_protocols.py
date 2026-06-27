"""Shared protocol capability API."""

from fastapi import APIRouter

from app.service.shared_protocols.registry import list_protocol_capabilities

router = APIRouter(prefix="/api/shared-protocols", tags=["shared-protocols"])


@router.get("")
def list_capabilities():
    return list_protocol_capabilities()
