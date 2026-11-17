import XCTest
@testable import FamilyFinance

/// 解码 Mac 端 core/mobile.py 生成的合成快照（ios-app/scripts/make_fixture.py），确认字段名和格式对得上。
final class SnapshotTests: XCTestCase {
    private func fixture() throws -> Snapshot {
        let url = try XCTUnwrap(Bundle(for: SnapshotTests.self).url(forResource: "snapshot-synthetic", withExtension: "json"))
        return try Snapshot.decode(Data(contentsOf: url))
    }

    func testDecodesEverySection() throws {
        let s = try fixture()
        XCTAssertEqual(s.version, 1)
        XCTAssertEqual(s.certificates.first?.key, "family")
        XCTAssertTrue(s.certificates.first?.net.usd.hasPrefix("$") ?? false)
        XCTAssertTrue(Set(Snapshot.compositionViews).isSubset(of: Set(s.composition.keys)))
        XCTAssertEqual(s.composition["按资产类别"]?.reduce(0) { $0 + $1.bp }, 10000)
        XCTAssertFalse(s.liquidity.isEmpty)
        XCTAssertEqual(s.trend.count, 3)
        XCTAssertTrue(s.holdings.contains { $0.key == "aapl" })
        XCTAssertFalse(s.members.me.isEmpty)
    }

    func testPlanMilestonesAndCountdownTarget() throws {
        let p = try XCTUnwrap(try fixture().plans.first)
        XCTAssertEqual(p.milestones.map(\.short), ["改单截止", "定价", "确认截止"])
        let cutoff = try XCTUnwrap(p.milestones[0].atDate)
        XCTAssertEqual(cutoff, ISO.date("2026-12-08T21:00:00Z"))          // 16:00 ET = 21:00 UTC（12 月是标准时间）
        XCTAssertEqual(p.upcoming(at: cutoff.addingTimeInterval(-1))?.short, "改单截止")
        XCTAssertEqual(p.upcoming(at: cutoff.addingTimeInterval(1))?.short, "定价")   // 只写日期的节点到当天结束才算过
        XCTAssertNil(p.upcoming(at: ISO.date("2026-12-10T12:01:00Z")!))
    }

    /// 展示模式靠快照里的 demo 标记分清示例数据和真实数据：真快照没有这个字段。
    func testDemoFlagMarksSampleData() throws {
        XCTAssertFalse(try fixture().isDemo)
        let url = try XCTUnwrap(Bundle(for: SnapshotTests.self).url(forResource: "snapshot-synthetic", withExtension: "json"))
        var obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        obj["demo"] = true
        XCTAssertTrue(try Snapshot.decode(JSONSerialization.data(withJSONObject: obj)).isDemo)
    }

    func testFaceValueSplitsDollarWholeAndCents() {
        XCTAssertEqual(zeroed("+$123,456.78"), "+$000,000.00")
    }
}
