"""人工审核服务 — 行级编辑、版本合并、审核状态管理"""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit


class ReviewService:

    def __init__(self, db: Session):
        self._db = db

    def start_review(self, curated_dataset_id: str, reviewer_id: str | None = None) -> CuratedReview:
        """新建审核记录，状态设为 pending"""
        ds = self._get_dataset_or_raise(curated_dataset_id)

        review = CuratedReview(
            curated_dataset_id=curated_dataset_id,
            reviewer_id=reviewer_id,
            status="pending",
        )
        self._db.add(review)
        ds.status = "in_review"
        self._db.commit()
        self._db.refresh(review)
        return review

    def edit_row(
        self,
        review_id: str,
        row_pk: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
    ) -> CuratedRowEdit:
        """记录单行单字段的修改"""
        review = self._get_review_or_raise(review_id)

        edit = CuratedRowEdit(
            review_id=review_id,
            row_pk=row_pk,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
        )
        self._db.add(edit)
        self._db.commit()
        self._db.refresh(edit)
        return edit

    def batch_edit_rows(self, review_id: str, edits: list[dict]) -> list[CuratedRowEdit]:
        """批量提交行编辑
        edits 格式：[{"row_pk": "...", "field_name": "...", "old_value": "...", "new_value": "..."}]
        """
        self._get_review_or_raise(review_id)
        results = []
        for e in edits:
            edit = CuratedRowEdit(
                review_id=review_id,
                row_pk=e["row_pk"],
                field_name=e["field_name"],
                old_value=e.get("old_value"),
                new_value=e.get("new_value"),
            )
            self._db.add(edit)
            results.append(edit)
        self._db.commit()
        return results

    def approve(self, review_id: str, notes: str = "") -> CuratedReview:
        """审核通过 — 将数据集状态改为 approved"""
        review = self._get_review_or_raise(review_id)
        review.status = "approved"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)

        ds = self._db.query(CuratedDataset).filter(
            CuratedDataset.id == review.curated_dataset_id
        ).first()
        if ds:
            ds.status = "approved"

        self._db.commit()
        self._db.refresh(review)
        return review

    def reject(self, review_id: str, notes: str = "") -> CuratedReview:
        """审核拒绝"""
        review = self._get_review_or_raise(review_id)
        review.status = "rejected"
        review.notes = notes
        review.decided_at = datetime.now(timezone.utc)

        ds = self._db.query(CuratedDataset).filter(
            CuratedDataset.id == review.curated_dataset_id
        ).first()
        if ds:
            ds.status = "rejected"

        self._db.commit()
        self._db.refresh(review)
        return review

    def get_edits(self, review_id: str) -> list[CuratedRowEdit]:
        """获取审核下的所有行编辑记录"""
        return self._db.query(CuratedRowEdit).filter(
            CuratedRowEdit.review_id == review_id
        ).all()

    def apply_edits_to_snapshot(self, review_id: str, original_data: list[dict]) -> list[dict]:
        """将行编辑应用到数据快照，返回修改后的数据（不修改数据库原始存储）"""
        edits = self.get_edits(review_id)
        if not edits:
            return original_data

        # 按 row_pk 分组编辑
        edit_map: dict[str, dict[str, str | None]] = {}
        for edit in edits:
            if edit.row_pk not in edit_map:
                edit_map[edit.row_pk] = {}
            edit_map[edit.row_pk][edit.field_name] = edit.new_value

        result = []
        for row in original_data:
            row_pk = str(row.get("id", row.get("__pk__", "")))
            if row_pk in edit_map:
                row = dict(row)
                for field, new_val in edit_map[row_pk].items():
                    if new_val is None:
                        row.pop(field, None)  # 删除字段
                    else:
                        row[field] = new_val
            result.append(row)

        return result

    # ── 私有辅助 ──────────────────────────────────────────────────

    def _get_dataset_or_raise(self, dataset_id: str) -> CuratedDataset:
        ds = self._db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
        if not ds:
            from fastapi import HTTPException
            raise HTTPException(404, f"Curated dataset {dataset_id} not found")
        return ds

    def _get_review_or_raise(self, review_id: str) -> CuratedReview:
        review = self._db.query(CuratedReview).filter(CuratedReview.id == review_id).first()
        if not review:
            from fastapi import HTTPException
            raise HTTPException(404, f"Review {review_id} not found")
        return review
