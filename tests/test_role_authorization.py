from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from app import auth


@pytest.mark.parametrize('role', ['sponsor', 'network', 'pharmacy'])
def test_non_clinical_role_cannot_read_clinical_routes(role):
    with patch.object(auth, 'read', return_value={'role': role}):
        with pytest.raises(HTTPException) as err:
            auth.require_role('doctor')(SimpleNamespace())
    assert err.value.status_code == 403


def test_named_role_allowed_and_empty_code_refused():
    with patch.object(auth, 'read', return_value={'role': 'doctor'}):
        assert auth.require_role('doctor')(SimpleNamespace())['role'] == 'doctor'
    with patch.object(auth, 'settings', SimpleNamespace(demo_access_code='')):
        assert not auth.check_code('')


def test_sponsor_cannot_promote_itself_to_doctor():
    from app.main import RoleIn, switch_role
    from fastapi import Response
    with pytest.raises(HTTPException) as err:
        switch_role(RoleIn(role='doctor'), Response(), {'role': 'sponsor'})
    assert err.value.status_code == 403


def test_clinical_router_rejects_sponsor_session():
    import asyncio
    import httpx
    from app.main import app
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            client.cookies.set(auth.COOKIE, auth._serializer.dumps({'v': 1, 'role': 'sponsor'}))
            assert (await client.get('/api/schedule')).status_code == 403
            assert (await client.get('/api/patients/p1')).status_code == 403
    asyncio.run(run())
