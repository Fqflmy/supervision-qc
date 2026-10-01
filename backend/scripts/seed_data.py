# -*- coding: utf-8 -*-
"""种子数据：把示例规范条款写入知识库并建立索引（SRS FR-KB-01~06）。

用法：
    python scripts/seed_data.py            # 幂等：已存在则跳过
    python scripts/seed_data.py --reset    # 先清理示例文档再重建

样本内容为公开规范的常见条款表述，仅用于系统自检与演示，不作为工程依据。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.constants import DocStatus, RegionLevel  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.db.models import DocVersion, KnowledgeBase, Project, SpecDoc, User  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.services.ingest import ingest_document  # noqa: E402
from app.services.storage import get_storage  # noqa: E402

SPECS: list[dict] = [
    {
        "spec_code": "GB 50204-2015",
        "spec_name": "混凝土结构工程施工质量验收规范",
        "issuer": "中华人民共和国住房和城乡建设部",
        "specialty": "结构工程",
        "region_level": RegionLevel.NATIONAL.value,
        "scope": "适用于混凝土结构工程施工质量的验收。",
        "content": """第5章 混凝土分项工程
5.1 一般规定
5.1.1 混凝土分项工程验收应包括原材料、配合比设计、混凝土施工等分项工程的质量验收。
5.1.2 混凝土结构工程施工前，施工单位应编制施工技术方案，并经监理单位审查批准。
5.2 原材料
5.2.1 水泥进场时应对其品种、级别、包装或散装仓号、出厂日期等进行检查，并应对其强度、安定性及其他必要的性能指标进行复验，其质量必须符合现行国家标准《通用硅酸盐水泥》GB 175 的规定。
5.2.2 混凝土中掺用外加剂的质量及应用技术应符合现行国家标准《混凝土外加剂》GB 8076 的规定。钢筋混凝土结构中，当使用含氯化物的外加剂时，混凝土中氯化物的总含量应符合现行国家标准《混凝土质量控制标准》GB 50164 的规定。
5.2.3 混凝土原材料应按进料批次进行检验，检验批的划分应符合下列规定：同一工程、同一配合比的混凝土，其原材料检验批不应少于一次。
5.3 混凝土施工
5.3.1 混凝土的强度等级必须符合设计要求。用于检验混凝土强度的试件应在浇筑地点随机抽取。
5.3.2 混凝土运输、浇筑及间歇的全部时间不应超过混凝土的初凝时间。同一施工段的混凝土应连续浇筑，并应在底层混凝土初凝之前将上一层混凝土浇筑完毕。
5.3.3 混凝土浇筑时的入模温度不宜高于30℃，不宜低于5℃。对大体积混凝土，入模温度不宜高于28℃，并应采取温控措施控制混凝土内外温差。
5.3.4 混凝土浇筑完毕后，应按施工技术方案及时采取有效的养护措施，并应符合下列规定：应在浇筑完毕后的12h以内对混凝土加以覆盖并保湿养护；混凝土浇水养护的时间不得少于7d；对掺用缓凝型外加剂或有抗渗要求的混凝土，不得少于14d。
5.3.5 混凝土浇筑过程中应按规定留置标准养护试件和同条件养护试件，试件的留置组数应符合现行国家标准的规定。
第6章 现浇结构分项工程
6.1 一般规定
6.1.1 现浇结构的外观质量不应有严重缺陷。对已经出现的严重缺陷，应由施工单位提出技术处理方案，并经监理单位认可后进行处理。
6.1.2 现浇结构不应有影响结构性能和使用功能的尺寸偏差。
6.2 外观质量
6.2.1 现浇结构的外观质量不宜有一般缺陷。对已经出现的一般缺陷，应由施工单位按技术处理方案进行处理，并重新检查验收。
6.2.2 现浇结构拆模后的外观质量缺陷应由监理单位、施工单位等各方根据其对结构性能和使用功能影响的严重程度按表6.2.2确定。
第7章 混凝土结构子分部工程
7.1 结构实体检验
7.1.1 对涉及混凝土结构安全的有代表性的部位应进行结构实体检验。结构实体检验应包括混凝土强度、钢筋保护层厚度、结构位置与尺寸偏差以及合同约定的项目。
7.1.2 结构实体混凝土强度应按不同强度等级分别检验，检验方法宜采用同条件养护试件方法。
7.1.3 钢筋保护层厚度的检验，应由监理单位见证，施工单位项目专业技术负责人组织，在现场进行检测。
7.2 子分部工程验收
7.2.1 混凝土结构子分部工程施工质量验收合格应符合下列规定：所含分项工程质量均应验收合格；质量控制资料应完整；观感质量验收应符合要求。
7.2.2 混凝土结构子分部工程验收时，应提供下列资料：设计变更文件；原材料出厂合格证及进场复验报告；混凝土试件性能试验报告；隐蔽工程验收记录；分项工程验收记录。""",
    },
    {
        "spec_code": "GB 50300-2013",
        "spec_name": "建筑工程施工质量验收统一标准",
        "issuer": "中华人民共和国住房和城乡建设部",
        "specialty": "通用",
        "region_level": RegionLevel.NATIONAL.value,
        "scope": "适用于建筑工程施工质量的验收。",
        "content": """第3章 基本规定
3.0.1 建筑工程施工质量验收应在施工单位自检合格的基础上进行。
3.0.2 参加工程施工质量验收的各方人员应具备相应的资格。
3.0.3 建筑工程施工质量验收应按检验批、分项工程、分部工程、单位工程依次进行。
3.0.4 检验批的质量应按主控项目和一般项目验收。主控项目是指建筑工程中对安全、节能、环境保护和主要使用功能起决定性作用的检验项目。
3.0.5 隐蔽工程在隐蔽前应由施工单位通知监理单位进行验收，并应形成验收文件，验收合格后方可继续施工。
3.0.6 涉及结构安全的试块、试件及有关材料，应按规定进行见证取样检测。
3.0.7 检验批合格质量标准应符合下列规定：主控项目的质量经抽样检验均应合格；一般项目的质量经抽样检验合格；具有完整的施工操作依据、质量检查记录。
3.0.8 分项工程质量验收合格应符合下列规定：所含检验批的质量均应验收合格；所含检验批的质量验收记录应完整。
第5章 建筑工程质量验收的划分
5.0.1 建筑工程质量验收应划分为单位工程、分部工程、分项工程和检验批。
5.0.2 单位工程应按下列原则划分：具备独立施工条件并能形成独立使用功能的建筑物为一个单位工程。
5.0.3 分部工程应按下列原则划分：按专业性质、建筑部位确定。
第6章 建筑工程质量验收
6.0.1 检验批质量验收合格应符合本标准第3.0.7条的规定。
6.0.2 当建筑工程质量不符合要求时，应按下列规定进行处理：经返工或返修的检验批，应重新进行验收；经有资质的检测机构检测鉴定能够达到设计要求的检验批，应予以验收；经返修或加固处理的分项、分部工程，虽然改变外形尺寸但仍能满足安全使用要求，可按技术处理方案和协商文件的要求予以验收。
6.0.3 通过返修或加固处理仍不能满足安全使用要求的分部工程、单位工程，严禁验收。
6.0.4 单位工程质量验收合格应符合下列规定：所含分部工程的质量均应验收合格；质量控制资料应完整；所含分部工程中有关安全、节能、环境保护和主要使用功能的检验资料应完整；主要使用功能的抽查结果应符合相关专业验收规范的规定；观感质量应符合要求。""",
    },
    {
        "spec_code": "GB 50202-2018",
        "spec_name": "建筑地基基础工程施工质量验收标准",
        "issuer": "中华人民共和国住房和城乡建设部",
        "specialty": "地基基础",
        "region_level": RegionLevel.NATIONAL.value,
        "scope": "适用于建筑地基基础工程施工质量的验收。",
        "content": """第4章 地基工程
4.1 一般规定
4.1.1 地基基础工程施工前，应具备完备的地质勘察资料和设计文件。
4.1.2 地基基础工程验收应在施工单位自检合格的基础上进行，并应提供下列资料：岩土工程勘察报告；设计文件及设计变更文件；原材料质量证明文件及复验报告；施工记录及检测报告。
4.1.3 地基基础工程施工过程中，应对下列项目进行隐蔽工程验收：地基处理；桩基础；基坑支护。
4.2 素土与灰土地基
4.2.1 素土、灰土地基施工质量检验应符合下列规定：地基承载力应符合设计要求；压实系数应符合设计要求。
4.2.2 灰土土料、石灰或水泥等材料的质量应符合设计要求，配合比应通过试验确定。
第5章 桩基础工程
5.1 一般规定
5.1.1 桩位的放样允许偏差应符合设计要求，群桩基础的桩位允许偏差应符合本标准的规定。
5.1.2 灌注桩的桩身质量应进行检验，检验数量不应少于总桩数的20%，且不应少于5根。
5.1.3 工程桩应进行承载力检验。对于地基基础设计等级为甲级或地质条件复杂、成桩质量可靠性较低的灌注桩，检验桩数不应少于总桩数的1%，且不应少于3根。
5.2 灌注桩
5.2.1 灌注桩成孔后应进行清孔，孔底沉渣厚度应符合设计要求。端承桩的沉渣厚度不应大于50mm。
5.2.2 钢筋笼的制作与安装应符合设计要求，钢筋笼主筋的保护层厚度不应小于50mm。
5.2.3 水下混凝土的强度等级应比设计强度等级提高一级配制，坍落度宜为180mm~220mm。
第6章 基坑工程
6.1 一般规定
6.1.1 基坑工程施工前应编制专项施工方案，并应经专家论证的按规定进行论证。
6.1.2 基坑支护结构应进行变形监测，监测数据超过报警值时应立即采取措施。
6.2 排桩与地下连续墙
6.2.1 排桩的桩位偏差不应大于50mm，桩身垂直度偏差不应大于1/100。
6.2.2 地下连续墙的墙身垂直度偏差不应大于1/300。""",
    },
    {
        "spec_code": "JGJ 130-2011",
        "spec_name": "建筑施工扣件式钢管脚手架安全技术规范",
        "issuer": "中华人民共和国住房和城乡建设部",
        "specialty": "施工安全",
        "region_level": RegionLevel.INDUSTRY.value,
        "scope": "适用于建筑工程中扣件式钢管脚手架的设计、施工与验收。",
        "content": """第5章 设计计算
5.1 一般规定
5.1.1 脚手架的承载能力应按概率极限状态设计法的要求进行设计。
5.1.2 脚手架立杆的稳定性应按下列公式计算，并应符合本规范的要求。
第6章 构造要求
6.1 立杆
6.1.1 脚手架立杆的纵距不应大于1.5m，横距不应大于1.2m。每个立杆底部应设置底座或垫板。
6.1.2 立杆顶端应高出女儿墙上皮1.0m，高出檐口上皮1.5m。
6.2 纵向水平杆与横向水平杆
6.2.1 纵向水平杆应设置在立杆内侧，其长度不宜小于3跨。纵向水平杆接长宜采用对接扣件连接。
6.2.2 主节点处必须设置一根横向水平杆，用直角扣件扣接且严禁拆除。
6.3 剪刀撑与横向斜撑
6.3.1 高度在24m以下的单、双排脚手架，均必须在外侧两端、转角及中间间隔不超过15m的立面上，各设置一道剪刀撑，并应由底至顶连续设置。
6.3.2 高度在24m及以上的双排脚手架应在外侧全立面连续设置剪刀撑。
6.4 连墙件
6.4.1 连墙件必须采用可承受拉力和压力的构件，严禁使用仅有拉筋的柔性连墙件。
6.4.2 连墙件的布置应满足下列要求：宜靠近主节点设置，偏离主节点的距离不应大于300mm；应从底层第一步纵向水平杆处开始设置。
6.4.3 对高度在24m以上的双排脚手架，应采用刚性连墙件与建筑物可靠连接。
第8章 检查与验收
8.1 检查与验收
8.1.1 脚手架及其地基基础应在下列阶段进行检查与验收：基础完工后及脚手架搭设前；作业层上施加荷载前；每搭设完6m~8m高度后；达到设计高度后；遇有六级及以上大风或大雨后；停用超过一个月。
8.1.2 脚手架使用中，应定期检查下列项目：杆件的设置和连接、连墙件、支撑、门洞桁架等的构造是否符合要求；扣件螺栓是否松动；安全防护措施是否符合要求。
8.1.3 扣件螺栓的拧紧扭力矩不应小于40N·m，且不应大于65N·m。""",
    },
]


async def seed(reset: bool = False) -> int:
    setup_logging("INFO", json_output=False)
    if not init_db():
        print("[FAIL] 数据库不可用，请先启动 PostgreSQL（deploy/docker-compose-infra.yml）")
        return 2

    storage = get_storage()
    written = 0

    with session_scope() as session:
        # 基础组织数据
        admin = session.execute(select(User).where(User.username == "admin")).scalars().first()
        if admin is None:
            if not settings.seed_default_admin:
                print("[跳过] SUPERVISION_SEED_DEFAULT_ADMIN=false，不创建默认管理员账号")
            else:
                from app.core.security import hash_password

                # 密码来自配置（SUPERVISION_SEED_DEFAULT_ADMIN_PASSWORD），不再硬编码。
                # 默认值 Admin@12345 已公开在仓库文档中，仅适用于开发/演示环境。
                admin_password = settings.seed_default_admin_password
                admin = User(
                    username="admin",
                    full_name="系统管理员",
                    password_hash=hash_password(admin_password),
                    role="admin",
                    specialties=["结构工程"],
                )
                session.add(admin)
                session.flush()
                if admin_password == "Admin@12345":
                    print("[OK] 创建管理员账号 admin / Admin@12345（默认密码，仅限开发环境，上线前必须修改）")
                else:
                    print("[OK] 创建管理员账号 admin（密码取自 SUPERVISION_SEED_DEFAULT_ADMIN_PASSWORD）")

        project = session.execute(select(Project).where(Project.code == "DEMO-001")).scalars().first()
        if project is None:
            project = Project(
                code="DEMO-001",
                name="示范工程·某住宅小区 1# 楼",
                owner_org="某建设单位",
                supervision_org="某监理有限公司",
                specialty="结构工程",
            )
            session.add(project)
            session.flush()
            print("[OK] 创建示例项目 DEMO-001")

        kb = session.execute(select(KnowledgeBase).where(KnowledgeBase.code == "KB-NATIONAL")).scalars().first()
        if kb is None:
            kb = KnowledgeBase(
                code="KB-NATIONAL",
                name="国家现行标准库",
                specialty="通用",
                description="施工质量验收类国家现行标准",
            )
            session.add(kb)
            session.flush()
            print("[OK] 创建知识库 KB-NATIONAL")

        for spec in SPECS:
            existing = session.execute(
                select(SpecDoc).where(SpecDoc.spec_code == spec["spec_code"])
            ).scalars().first()
            if existing is not None and not reset:
                print(f"[SKIP] {spec['spec_code']} 已存在（doc_id={existing.id}）")
                continue
            if existing is not None and reset:
                from app.db.models import DocChunk

                versions = session.execute(
                    select(DocVersion).where(DocVersion.doc_id == existing.id)
                ).scalars().all()
                for version in versions:
                    session.query(DocChunk).filter(DocChunk.doc_version_id == version.id).delete()
                    session.delete(version)
                session.delete(existing)
                session.flush()
                print(f"[RESET] 已清理 {spec['spec_code']}")

            # 注意：不使用 tempfile（沙箱内 AppData\Temp 无法清理），
            # 统一放在项目 var/seed 下，便于排查与复用。
            seed_dir = BACKEND.parent / "var" / "seed"
            seed_dir.mkdir(parents=True, exist_ok=True)
            txt_path = seed_dir / f"{spec['spec_code'].replace(' ', '_')}.txt"
            txt_path.write_text(spec["content"], encoding="utf-8")
            with txt_path.open("rb") as fh:
                relative, digest, size = storage.save(fh, txt_path.name, subdir="seed")

            doc = SpecDoc(
                kb_id=kb.id,
                spec_code=spec["spec_code"],
                spec_name=spec["spec_name"],
                issuer=spec["issuer"],
                specialty=spec["specialty"],
                region_level=spec["region_level"],
                scope=spec["scope"],
                status=DocStatus.PUBLISHED.value,
                # 关闭默认管理员时 admin 可能不存在，uploader_id 允许为空
                uploader_id=getattr(admin, "id", None),
            )
            session.add(doc)
            session.flush()

            version = DocVersion(
                doc_id=doc.id,
                version_label="2015版" if "2015" in spec["spec_code"] else "现行版",
                file_name=txt_path.name,
                file_path=relative,
                file_size=size,
                file_hash=digest,
                file_type="txt",
                parse_status="pending",
                is_current=True,
            )
            session.add(version)
            session.flush()

            result = await ingest_document(session, version, doc)
            written += 1
            print(
                f"[OK] {spec['spec_code']} 入库：chunks={result.chunk_count} "
                f"pages={result.page_count} namespace={result.namespace} "
                f"degraded_embedding={result.degraded_embedding}"
            )

    print(f"[DONE] 本次写入 {written} 份规范")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="写入示例规范并建立索引")
    parser.add_argument("--reset", action="store_true", help="先清理示例文档再重建")
    args = parser.parse_args()
    return asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    raise SystemExit(main())
