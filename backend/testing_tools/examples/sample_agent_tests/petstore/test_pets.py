STATUSES = {"available", "sold"}


def check_pet(pet):
    assert isinstance(pet["id"], int)
    assert isinstance(pet["name"], str)
    assert pet["status"] in STATUSES


def test_list_pets(api):
    r = api.get("/pets")
    assert r.status_code == 200
    for pet in r.json():
        check_pet(pet)


def test_list_pets_limit(api):
    r = api.get("/pets", params={"limit": 1})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_limit_above_maximum_is_rejected(api):
    assert api.get("/pets", params={"limit": 101}).status_code == 400


def test_get_pet(api):
    r = api.get("/pets/1")
    assert r.status_code == 200
    check_pet(r.json())
    assert r.json()["id"] == 1


def test_get_missing_pet(api):
    r = api.get("/pets/999")
    assert r.status_code == 404
    assert isinstance(r.json()["detail"], str)


def test_create_pet(api):
    r = api.post("/pets", json={"name": "tom", "tag": "cat"})
    assert r.status_code == 201
    check_pet(r.json())


def test_create_pet_with_empty_name_is_rejected(api):
    assert api.post("/pets", json={"name": ""}).status_code == 422


def test_delete_missing_pet(api):
    assert api.delete("/pets/999").status_code == 404
