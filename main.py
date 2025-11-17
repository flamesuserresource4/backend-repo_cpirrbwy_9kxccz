import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

# Stripe (optional, for checkout)
STRIPE_SECRET = os.getenv("STRIPE_SECRET_KEY")
stripe = None
if STRIPE_SECRET:
    try:
        import stripe as stripe_sdk
        stripe_sdk.api_key = STRIPE_SECRET
        stripe = stripe_sdk
    except Exception:
        stripe = None

app = FastAPI(title="Skinny Fit Tea API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------- Utils -------------------------

def serialize_doc(doc):
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    # convert any nested ObjectIds if needed later
    return doc


# ------------------------- Schemas -------------------------

class ProductIn(BaseModel):
    title: str
    description: Optional[str] = None
    price: float = Field(ge=0)
    compare_at_price: Optional[float] = Field(default=None, ge=0)
    category: str = "Tea"
    in_stock: bool = True
    stock: int = 100
    images: List[str] = []
    tags: List[str] = []

class Product(ProductIn):
    id: str

class CartItem(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, default=1)

class CheckoutRequest(BaseModel):
    items: List[CartItem]
    customer_email: Optional[str] = None
    success_url: str
    cancel_url: str


# ------------------------- Basic -------------------------

@app.get("/")
def root():
    return {"name": "Skinny Fit Tea API", "status": "ok"}

@app.get("/test")
def test_database():
    resp = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "collections": []
    }
    try:
        if db is not None:
            resp["database"] = "✅ Connected"
            resp["collections"] = db.list_collection_names()
    except Exception as e:
        resp["database"] = f"⚠️ Error: {str(e)[:120]}"
    return resp


# ------------------------- Products -------------------------

@app.get("/api/products", response_model=List[Product])
def list_products():
    docs = get_documents("product", {})
    return [Product(**serialize_doc(d)) for d in docs]

@app.post("/api/products", response_model=Product)
def create_product(payload: ProductIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    data = payload.model_dump()
    new_id = create_document("product", data)
    saved = db["product"].find_one({"_id": db.command("serverStatus")["localTime"].__class__.__mro__[1].from_json(new_id)})
    # Fallback fetch by _id string cast
    try:
        from bson import ObjectId
        saved = db["product"].find_one({"_id": ObjectId(new_id)})
    except Exception:
        saved = db["product"].find_one({})
    return Product(**serialize_doc(saved))

@app.get("/api/products/{product_id}", response_model=Product)
def get_product(product_id: str):
    from bson import ObjectId
    doc = db["product"].find_one({"_id": ObjectId(product_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(**serialize_doc(doc))

@app.put("/api/products/{product_id}", response_model=Product)
def update_product(product_id: str, payload: ProductIn):
    from bson import ObjectId
    result = db["product"].update_one({"_id": ObjectId(product_id)}, {"$set": payload.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    doc = db["product"].find_one({"_id": ObjectId(product_id)})
    return Product(**serialize_doc(doc))

@app.delete("/api/products/{product_id}")
def delete_product(product_id: str):
    from bson import ObjectId
    result = db["product"].delete_one({"_id": ObjectId(product_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"success": True}


# ------------------------- Checkout with Stripe -------------------------

@app.post("/api/checkout/create-session")
def create_checkout_session(req: CheckoutRequest):
    if stripe is None:
        raise HTTPException(status_code=400, detail="Stripe is not configured. Set STRIPE_SECRET_KEY environment variable.")

    # Build line items from product ids to prevent price tampering
    from bson import ObjectId
    line_items = []
    for item in req.items:
        prod = db["product"].find_one({"_id": ObjectId(item.product_id)})
        if not prod:
            raise HTTPException(status_code=404, detail=f"Product not found: {item.product_id}")
        unit_amount = int(float(prod.get("price", 0)) * 100)
        line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": prod.get("title", "Product"),
                    "images": prod.get("images", [])[:1]
                },
                "unit_amount": unit_amount,
            },
            "quantity": item.quantity,
        })

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=line_items,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
            customer_email=req.customer_email,
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
