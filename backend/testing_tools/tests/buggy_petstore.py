"""Tiny pet service with deliberate contract violations, for real-tool tests.

Contract: petstore_openapi.yaml. Bugs on purpose:
- GET /pets/1 returns `id` as a string (schema says integer);
- POST /pets with an empty name answers 500;
- an unparsable petId answers 422, which the contract doesn't document.
"""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


def create_app() -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    pets: dict[int, dict[str, Any]] = {1: {"id": 1, "name": "rex", "tag": "dog"}}

    @app.get("/pets")
    def list_pets() -> list[dict[str, Any]]:
        return list(pets.values())

    @app.post("/pets", status_code=201)
    async def create_pet(request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "bad json"}, status_code=422)
        if not isinstance(body, dict) or not isinstance(body.get("name"), str):
            return JSONResponse({"detail": "name is required"}, status_code=422)
        if body["name"] == "":
            return JSONResponse({"detail": "boom"}, status_code=500)
        pet_id = max(pets, default=0) + 1
        pets[pet_id] = {"id": pet_id, "name": body["name"]}
        return pets[pet_id]

    @app.get("/pets/{pet_id}")
    def get_pet(pet_id: int) -> Any:
        pet = pets.get(pet_id)
        if pet is None:
            raise HTTPException(404, "not found")
        if pet_id == 1:
            return {"id": "1", "name": pet["name"]}
        return pet

    return app
