// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

contract Factoring {
    enum Status { NONE, ACTIVE, RESERVED, FINANCED, SETTLED }
    struct Deal {
        address supplier;
        address buyer;
        address factor;
        address token;
        uint256 financingAmount;
        uint256 claimAmount;
        uint256 dueDate;
        bytes32 documentHash;
        Status status;
        uint256 commissionAmount;
    }
    struct Attestation {
        bytes32 dealId;
        bytes32 documentHash;
        bool deliveryAccepted;
        bool claimAssigned;
        bool buyerNotified;
        bool financingApproved;
        uint256 issuedAt;
        uint256 expiresAt;
        uint256 nonce;
    }
    bytes32 public constant TYPEHASH = keccak256("Attestation(bytes32 dealId,bytes32 documentHash,bool deliveryAccepted,bool claimAssigned,bool buyerNotified,bool financingApproved,uint256 issuedAt,uint256 expiresAt,uint256 nonce)");
    uint256 public constant CONTRACT_VERSION = 5;
    address public immutable oracle;
    mapping(bytes32 => Deal) public deals;
    mapping(uint256 => bool) public usedNonces;
    mapping(address => uint256) public totalReserved;
    bool private entered;
    event DealCreated(bytes32 indexed dealId);
    event FinancingReserved(bytes32 indexed dealId, uint256 amount);
    event FinancingExecuted(bytes32 indexed dealId, address indexed supplier, uint256 amount);
    event DealSettled(bytes32 indexed dealId, address indexed buyer, uint256 paidAmount, uint256 factorPayment, uint256 supplierRemainder, uint256 commissionAmount);

    constructor(address signer) {
        require(signer != address(0), "zero oracle");
        oracle = signer;
    }

    modifier nonReentrant() {
        require(!entered, "reentrancy");
        entered = true;
        _;
        entered = false;
    }

    function createDeal(bytes32 id, Deal calldata d) external {
        require(id != bytes32(0) && deals[id].status == Status.NONE, "existing deal");
        require(msg.sender == d.factor, "factor only");
        require(d.supplier != address(0) && d.buyer != address(0) && d.token.code.length > 0, "invalid parties");
        require(d.financingAmount > 0 && d.claimAmount >= d.financingAmount, "invalid amounts");
        require(d.commissionAmount <= d.claimAmount - d.financingAmount, "commission exceeds remainder");
        require(d.dueDate > block.timestamp && d.documentHash != bytes32(0), "invalid document or date");
        require(d.status == Status.ACTIVE, "invalid initial state");
        deals[id] = d;
        emit DealCreated(id);
    }

    function reserve(bytes32 id) external nonReentrant {
        Deal storage d = deals[id];
        require(d.status == Status.ACTIVE && msg.sender == d.factor, "cannot reserve");
        uint256 beforeBalance = IERC20(d.token).balanceOf(address(this));
        d.status = Status.RESERVED;
        totalReserved[d.token] += d.financingAmount;
        safeCall(d.token, abi.encodeCall(IERC20.transferFrom, (d.factor, address(this), d.financingAmount)));
        require(IERC20(d.token).balanceOf(address(this)) == beforeBalance + d.financingAmount, "inexact deposit");
        emit FinancingReserved(id, d.financingAmount);
    }

    function domainSeparator() public view returns (bytes32) {
        return keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256("VTB Oracle of Trust"), keccak256("2"), block.chainid, address(this)
        ));
    }

    function digest(Attestation calldata a) public view returns (bytes32) {
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator(), keccak256(abi.encode(TYPEHASH, a))));
    }

    function execute(Attestation calldata a, bytes calldata signature) external nonReentrant {
        Deal storage d = deals[a.dealId];
        require(d.status == Status.RESERVED, "deal not reserved");
        require(d.documentHash == a.documentHash, "document mismatch");
        require(a.deliveryAccepted && a.claimAssigned && a.buyerNotified && a.financingApproved, "missing fact");
        require(a.issuedAt <= block.timestamp && block.timestamp < a.expiresAt && a.issuedAt < a.expiresAt, "invalid lifetime");
        require(!usedNonces[a.nonce], "used nonce");
        require(signature.length == 65, "invalid signature length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        require(uint256(s) <= 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0 && (v == 27 || v == 28), "noncanonical signature");
        require(ecrecover(digest(a), v, r, s) == oracle, "untrusted signer");
        require(IERC20(d.token).balanceOf(address(this)) >= totalReserved[d.token], "insufficient reserve");
        d.status = Status.FINANCED;
        usedNonces[a.nonce] = true;
        totalReserved[d.token] -= d.financingAmount;
        safeCall(d.token, abi.encodeCall(IERC20.transfer, (d.supplier, d.financingAmount)));
        emit FinancingExecuted(a.dealId, d.supplier, d.financingAmount);
    }

    function repay(bytes32 id) external nonReentrant {
        Deal storage d = deals[id];
        require(d.status == Status.FINANCED && msg.sender == d.buyer, "financed buyer only");
        uint256 factorPayment = d.financingAmount + d.commissionAmount;
        uint256 supplierRemainder = d.claimAmount - factorPayment;
        uint256 beforeBalance = IERC20(d.token).balanceOf(address(this));
        d.status = Status.SETTLED;
        safeCall(d.token, abi.encodeCall(IERC20.transferFrom, (d.buyer, address(this), d.claimAmount)));
        require(IERC20(d.token).balanceOf(address(this)) == beforeBalance + d.claimAmount, "inexact repayment");
        safeCall(d.token, abi.encodeCall(IERC20.transfer, (d.factor, factorPayment)));
        if (supplierRemainder > 0) {
            safeCall(d.token, abi.encodeCall(IERC20.transfer, (d.supplier, supplierRemainder)));
        }
        require(IERC20(d.token).balanceOf(address(this)) == beforeBalance, "repayment touched reserves");
        emit DealSettled(id, d.buyer, d.claimAmount, factorPayment, supplierRemainder, d.commissionAmount);
    }

    function safeCall(address token, bytes memory data) private {
        (bool ok, bytes memory result) = token.call(data);
        require(ok && (result.length == 0 || abi.decode(result, (bool))), "token transfer failed");
    }
}
