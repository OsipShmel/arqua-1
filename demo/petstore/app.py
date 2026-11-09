"""Demo Petstore: openapi.yaml is the contract; defects P1-P4 (demo/README.md) are on purpose."""

from pathlib import Path as FilePath
from typing import Any

from fastapi import HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from common import contract_app

app = contract_app(FilePath(__file__).with_name("openapi.yaml"))

PETS: dict[int, dict[str, Any]] = {
    1: {"id": 1, "name": "rex", "tag": "dog", "status": "available"},
    2: {"id": 2, "name": "kitty", "tag": "cat", "status": "sold"},
}


@app.get("/pets")
def list_pets(limit: int = Query(20, ge=1)) -> Any:
    if limit > 100:
        # P1: the contract caps limit at 100 and promises 400; we crash instead
        return JSONResponse({"detail": "internal error"}, 500)
    return [PETS[k] for k in sorted(PETS)][:limit]


@app.post("/pets", status_code=201)
async def create_pet(request: Request) -> Any:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "body must be JSON"}, 422)
    if not isinstance(body, dict) or not isinstance(body.get("name"), str):
        return JSONResponse({"detail": "name is required"}, 422)
    tag = body.get("tag")
    if "tag" in body and (not isinstance(tag, str) or len(tag) > 20):
        return JSONResponse({"detail": "tag must be a string up to 20 chars"}, 422)
    name: str = body["name"]
    if len(name) > 50:
        return JSONResponse({"detail": "name is too long"}, 422)
    if name == "":
        # P2: an empty name is invalid (422 in the contract), but it crashes the service
        return JSONResponse({"detail": "internal error"}, 500)
    pet_id = max(PETS, default=0) + 1
    # P3: new pets are stored without the required `status` field
    PETS[pet_id] = {"id": pet_id, "name": name} | ({"tag": tag} if tag else {})
    return PETS[pet_id]


@app.get("/pets/{petId}")
def get_pet(petId: int = Path(ge=1)) -> Any:  # noqa: N803 - name from the contract
    pet = PETS.get(petId)
    if pet is None:
        raise HTTPException(404, "pet not found")
    return pet


@app.delete("/pets/{petId}", status_code=204)
def delete_pet(petId: int) -> Response:  # noqa: N803
    if petId < 1:
        return JSONResponse({"detail": "petId must be >= 1"}, 400)
    if PETS.pop(petId, None) is None:
        # P4: the contract promises 404 for an unknown pet; we answer 200
        return JSONResponse({"deleted": False}, 200)
    return Response(status_code=204)
