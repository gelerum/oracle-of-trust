// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Factoring} from "../src/Factoring.sol";
import {MockRuble} from "../src/MockRuble.sol";

interface Vm {
    function addr(uint256 key) external returns (address);
    function sign(uint256 key, bytes32 hash) external returns (uint8, bytes32, bytes32);
    function warp(uint256 timestamp) external;
    function chainId(uint256 id) external;
    function expectRevert() external;
    function prank(address account) external;
}

contract BrokenToken {
    mapping(address => uint256) public balanceOf;
    constructor() { balanceOf[msg.sender] = 9_000_000e6; }
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
    function transfer(address, uint256) external pure returns (bool) { return false; }
}

contract SelectiveToken is MockRuble {
    bool public failPayout;
    bool public shortDeposit;
    function setFailPayout(bool value) external { failPayout = value; }
    function setShortDeposit(bool value) external { shortDeposit = value; }
    function transfer(address to, uint256 amount) public override returns (bool) {
        if (failPayout) return false;
        return super.transfer(to, amount);
    }
    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        super.transferFrom(from, to, amount);
        if (shortDeposit) super.transfer(address(0xdead), 1);
        return true;
    }
}

contract FactoringTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 constant KEY = 123456;
    bytes32 constant ID = keccak256("deal");
    bytes32 constant DOC = keccak256("upd");
    address constant SUPPLIER = address(0x1234);
    MockRuble token;
    Factoring gateway;

    function setUp() public {
        vm.warp(1790000000);
        token = new MockRuble();
        gateway = new Factoring(vm.addr(KEY));
        create(gateway, ID);
        token.mint(address(this), 9_000_000e6);
        token.approve(address(gateway), 9_000_000e6);
        gateway.reserve(ID);
    }

    function create(Factoring target, bytes32 id) private {
        target.createDeal(id, Factoring.Deal(SUPPLIER, address(0x5678), address(this), address(token),
            9_000_000e6, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.ACTIVE, 200_000e6));
    }

    function attestation() private view returns (Factoring.Attestation memory) {
        return Factoring.Attestation(ID, DOC, true, true, true, true, block.timestamp, block.timestamp + 120, 1);
    }

    function sign(Factoring target, Factoring.Attestation memory a, uint256 key) private returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, target.digest(a));
        return abi.encodePacked(r, s, v);
    }

    function testFinancingExactlyOnce() public {
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY);
        gateway.execute(a, sig);
        require(token.balanceOf(SUPPLIER) == 9_000_000e6, "wrong supplier amount");
        require(gateway.usedNonces(1), "nonce not marked");
        (,,,,,,,, Factoring.Status status,) = gateway.deals(ID);
        require(status == Factoring.Status.FINANCED, "wrong financing status");
        require(gateway.totalReserved(address(token)) == 0, "reserve not released");
        vm.expectRevert();
        gateway.execute(a, sig);
        require(token.balanceOf(SUPPLIER) == 9_000_000e6, "double payment");
    }

    function testFuzzEachFlagRequired(uint8 flag) public {
        Factoring.Attestation memory a = attestation();
        flag %= 4;
        if (flag == 0) a.deliveryAccepted = false;
        if (flag == 1) a.claimAssigned = false;
        if (flag == 2) a.buyerNotified = false;
        if (flag == 3) a.financingApproved = false;
        bytes memory sig = sign(gateway, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testExpired() public {
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY);
        vm.warp(a.expiresAt);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testFutureIssued() public {
        Factoring.Attestation memory a = attestation();
        a.issuedAt += 1;
        bytes memory sig = sign(gateway, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testWrongSigner() public {
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY + 1);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testMalformedSignature() public {
        Factoring.Attestation memory a = attestation();
        vm.expectRevert();
        gateway.execute(a, hex"1234");
    }

    function testTamperedPayload() public {
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY);
        a.nonce = 999;
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testWrongDocument() public {
        Factoring.Attestation memory a = attestation();
        a.documentHash = keccak256("wrong");
        bytes memory sig = sign(gateway, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testWrongDeal() public {
        Factoring.Attestation memory a = attestation();
        a.dealId = keccak256("unknown");
        bytes memory sig = sign(gateway, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testWrongChainDomain() public {
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY);
        vm.chainId(block.chainid + 1);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testWrongContractDomain() public {
        Factoring other = new Factoring(vm.addr(KEY));
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(other, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testUnreservedDeal() public {
        bytes32 otherId = keccak256("unfunded");
        create(gateway, otherId);
        Factoring.Attestation memory a = attestation();
        a.dealId = otherId;
        bytes memory sig = sign(gateway, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }

    function testInsufficientFactorFunds() public {
        bytes32 otherId = keccak256("unfunded");
        create(gateway, otherId);
        token.approve(address(gateway), 9_000_000e6);
        vm.expectRevert();
        gateway.reserve(otherId);
        (,,,,,,,, Factoring.Status status,) = gateway.deals(otherId);
        require(status == Factoring.Status.ACTIVE, "failed reservation changed status");
    }

    function testImmutableAmountAndRecipient() public {
        Factoring.Deal memory changed = Factoring.Deal(address(0x9999), address(0x5678), address(this),
            address(token), 1, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.ACTIVE, 200_000e6);
        vm.expectRevert();
        gateway.createDeal(ID, changed);
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY);
        gateway.execute(a, sig);
        require(token.balanceOf(SUPPLIER) == 9_000_000e6, "changed payout");
        require(token.balanceOf(address(0x9999)) == 0, "changed recipient");
    }

    function testOnlyFactorCreatesAndReserves() public {
        Factoring.Deal memory d = Factoring.Deal(SUPPLIER, address(1), address(this), address(token),
            1, 2, block.timestamp + 1 days, DOC, Factoring.Status.ACTIVE, 1);
        vm.prank(address(0x9999));
        vm.expectRevert();
        gateway.createDeal(keccak256("unauthorized"), d);
        bytes32 otherId = keccak256("other");
        create(gateway, otherId);
        vm.prank(address(0x9999));
        vm.expectRevert();
        gateway.reserve(otherId);
    }

    function testNonceCannotMoveBetweenDeals() public {
        bytes32 otherId = keccak256("second");
        create(gateway, otherId);
        token.mint(address(this), 9_000_000e6);
        token.approve(address(gateway), 9_000_000e6);
        gateway.reserve(otherId);
        Factoring.Attestation memory a = attestation();
        gateway.execute(a, sign(gateway, a, KEY));
        a.dealId = otherId;
        bytes memory sig = sign(gateway, a, KEY);
        vm.expectRevert();
        gateway.execute(a, sig);
    }
    function testInsufficientExecutionReserve() public {
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(gateway, a, KEY);
        vm.prank(address(gateway));
        token.transfer(address(0x9999), 1);
        vm.expectRevert();
        gateway.execute(a, sig);
        require(!gateway.usedNonces(a.nonce), "nonce consumed after failure");
        require(token.balanceOf(SUPPLIER) == 0, "partial payout");
    }

    function testReserveCannotBeReused() public {
        vm.expectRevert();
        gateway.reserve(ID);
    }

    function testTransferFailureRollsBackStatusAndNonce() public {
        BrokenToken broken = new BrokenToken();
        Factoring other = new Factoring(vm.addr(KEY));
        other.createDeal(ID, Factoring.Deal(SUPPLIER, address(0x5678), address(this), address(broken),
            9_000_000e6, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.ACTIVE, 200_000e6));
        other.reserve(ID);
        Factoring.Attestation memory a = attestation();
        bytes memory sig = sign(other, a, KEY);
        vm.expectRevert();
        other.execute(a, sig);
        require(!other.usedNonces(a.nonce), "nonce consumed after failed transfer");
        require(other.totalReserved(address(broken)) == 9_000_000e6, "reserve lost");
        (,,,,,,,, Factoring.Status status,) = other.deals(ID);
        require(status == Factoring.Status.RESERVED, "status changed after failed transfer");
    }

    function finance() private {
        Factoring.Attestation memory a = attestation();
        gateway.execute(a, sign(gateway, a, KEY));
    }

    function pay() private {
        token.mint(address(0x5678), 10_000_000e6);
        vm.prank(address(0x5678));
        token.approve(address(gateway), 10_000_000e6);
        vm.prank(address(0x5678));
        gateway.repay(ID);
    }

    function testRepaymentAndFixedCommission() public {
        finance();
        pay();
        require(token.balanceOf(SUPPLIER) == 9_800_000e6, "supplier total");
        require(token.balanceOf(address(this)) == 9_200_000e6, "factor principal and commission");
        require(token.balanceOf(address(0x5678)) == 0, "buyer debt not repaid");
        require(token.balanceOf(address(gateway)) == 0, "unexpected escrow balance");
        (,,,,,,,, Factoring.Status status, uint256 fee) = gateway.deals(ID);
        require(status == Factoring.Status.SETTLED && fee == 200_000e6, "settlement state");
    }

    function testRepaymentCannotExecuteTwice() public {
        finance();
        pay();
        vm.prank(address(0x5678));
        vm.expectRevert();
        gateway.repay(ID);
        require(token.balanceOf(SUPPLIER) == 9_800_000e6, "double remainder");
    }

    function testOnlyBuyerCanRepay() public {
        finance();
        vm.expectRevert();
        gateway.repay(ID);
    }

    function testCannotRepayBeforeFinancing() public {
        vm.prank(address(0x5678));
        vm.expectRevert();
        gateway.repay(ID);
    }

    function testRepaymentNeedsFullFundsAndAllowance() public {
        finance();
        vm.prank(address(0x5678));
        token.approve(address(gateway), 10_000_000e6);
        vm.prank(address(0x5678));
        vm.expectRevert();
        gateway.repay(ID);
        token.mint(address(0x5678), 10_000_000e6);
        vm.prank(address(0x5678));
        token.approve(address(gateway), 9_999_999e6);
        vm.prank(address(0x5678));
        vm.expectRevert();
        gateway.repay(ID);
        (,,,,,,,, Factoring.Status status,) = gateway.deals(ID);
        require(status == Factoring.Status.FINANCED, "failed payment closed deal");
        require(token.balanceOf(address(0x5678)) == 10_000_000e6, "partial debit");
    }

    function testRepaymentPreservesOtherDealReserves() public {
        bytes32 otherId = keccak256("other reserve");
        create(gateway, otherId);
        token.mint(address(this), 9_000_000e6);
        token.approve(address(gateway), 9_000_000e6);
        gateway.reserve(otherId);
        finance();
        pay();
        require(gateway.totalReserved(address(token)) == 9_000_000e6, "other reserve changed");
        require(token.balanceOf(address(gateway)) == 9_000_000e6, "other funds spent");
    }

    function testCommissionCannotExceedRemainder() public {
        Factoring.Deal memory invalid = Factoring.Deal(SUPPLIER, address(0x5678), address(this), address(token),
            9_000_000e6, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.ACTIVE, 1_000_001e6);
        vm.expectRevert();
        gateway.createDeal(keccak256("invalid fee"), invalid);
    }

    function testLateRepaymentUsesAgreedFixedCommission() public {
        finance();
        vm.warp(block.timestamp + 61 days);
        pay();
        require(token.balanceOf(address(this)) == 9_200_000e6, "unexpected late fee");
    }

    function testFuzzCommissionAllocation(uint256 fee) public {
        fee %= 1_000_000e6 + 1;
        bytes32 otherId = keccak256("variable fee");
        gateway.createDeal(otherId, Factoring.Deal(SUPPLIER, address(0x5678), address(this), address(token),
            9_000_000e6, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.ACTIVE, fee));
        token.mint(address(this), 9_000_000e6);
        token.approve(address(gateway), 9_000_000e6);
        gateway.reserve(otherId);
        Factoring.Attestation memory a = attestation();
        a.dealId = otherId;
        gateway.execute(a, sign(gateway, a, KEY));
        token.mint(address(0x5678), 10_000_000e6);
        vm.prank(address(0x5678));
        token.approve(address(gateway), 10_000_000e6);
        vm.prank(address(0x5678));
        gateway.repay(otherId);
        require(token.balanceOf(SUPPLIER) == 10_000_000e6 - fee, "supplier allocation");
        require(token.balanceOf(address(this)) == 9_000_000e6 + fee, "factor allocation");
    }

    function testSettlementTransferErrorsRollBackEverything() public {
        SelectiveToken selected = new SelectiveToken();
        Factoring other = new Factoring(vm.addr(KEY));
        other.createDeal(ID, Factoring.Deal(SUPPLIER, address(0x5678), address(this), address(selected),
            9_000_000e6, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.ACTIVE, 200_000e6));
        selected.mint(address(this), 9_000_000e6);
        selected.approve(address(other), 9_000_000e6);
        other.reserve(ID);
        Factoring.Attestation memory a = attestation();
        other.execute(a, sign(other, a, KEY));
        selected.mint(address(0x5678), 10_000_000e6);
        vm.prank(address(0x5678));
        selected.approve(address(other), 10_000_000e6);
        selected.setFailPayout(true);
        vm.prank(address(0x5678));
        vm.expectRevert();
        other.repay(ID);
        selected.setFailPayout(false);
        selected.setShortDeposit(true);
        vm.prank(address(0x5678));
        vm.expectRevert();
        other.repay(ID);
        (,,,,,,,, Factoring.Status status,) = other.deals(ID);
        require(status == Factoring.Status.FINANCED, "failed settlement changed status");
        require(selected.balanceOf(address(0x5678)) == 10_000_000e6, "buyer funds not rolled back");
        require(selected.balanceOf(SUPPLIER) == 9_000_000e6, "partial remainder paid");
        require(selected.balanceOf(address(other)) == 0, "partial deposit retained");
    }

    function testReserveChangesActiveToReserved() public {
        bytes32 otherId = keccak256("reservation state");
        create(gateway, otherId);
        (,,,,,,,, Factoring.Status beforeStatus,) = gateway.deals(otherId);
        require(beforeStatus == Factoring.Status.ACTIVE, "wrong initial status");
        token.mint(address(this), 9_000_000e6);
        token.approve(address(gateway), 9_000_000e6);
        gateway.reserve(otherId);
        (,,,,,,,, Factoring.Status afterStatus,) = gateway.deals(otherId);
        require(afterStatus == Factoring.Status.RESERVED, "reservation status missing");
    }

    function testCannotCreateAlreadyReservedDeal() public {
        Factoring.Deal memory d = Factoring.Deal(SUPPLIER, address(0x5678), address(this), address(token),
            9_000_000e6, 10_000_000e6, block.timestamp + 60 days, DOC, Factoring.Status.RESERVED, 200_000e6);
        vm.expectRevert();
        gateway.createDeal(keccak256("fake reserve"), d);
    }

}
