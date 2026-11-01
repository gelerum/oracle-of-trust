// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract ObservationConsumer {
    struct Observation {
        bytes32 requestId;
        int256 value;
        uint256 issuedAt;
        uint256 expiresAt;
        uint256 nonce;
    }
    address public immutable oracle;
    mapping(bytes32 => int256) public readings;
    mapping(uint256 => bool) public usedNonces;
    bytes32 constant TYPEHASH = keccak256("Observation(bytes32 requestId,int256 value,uint256 issuedAt,uint256 expiresAt,uint256 nonce)");

    constructor(address signer) { oracle = signer; }

    function digest(Observation calldata o) public view returns (bytes32) {
        bytes32 domain = keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256("Independent Consumer"), keccak256("7"), block.chainid, address(this)));
        return keccak256(abi.encodePacked("\x19\x01", domain, keccak256(abi.encode(TYPEHASH, o))));
    }

    function accept(Observation calldata o, bytes calldata signature) external {
        require(o.issuedAt <= block.timestamp && block.timestamp < o.expiresAt, "invalid lifetime");
        require(!usedNonces[o.nonce] && signature.length == 65, "replay or invalid signature");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        require(uint256(s) <= 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0 && (v == 27 || v == 28), "invalid signature");
        require(ecrecover(digest(o), v, r, s) == oracle, "wrong signer");
        usedNonces[o.nonce] = true;
        readings[o.requestId] = o.value;
    }
}
