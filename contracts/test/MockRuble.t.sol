// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {MockRuble} from "../src/MockRuble.sol";

interface TokenVm {
    function prank(address sender) external;
    function expectRevert() external;
}

contract MockRubleTest {
    TokenVm constant VM = TokenVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant CENTRAL_BANK = address(0xcb);
    address constant FACTOR = address(0xfac);
    address constant BUYER = address(0xb0);
    MockRuble token;

    function setUp() public {
        VM.prank(CENTRAL_BANK);
        token = new MockRuble();
    }

    function testCentralBankIsIssuer() public view {
        require(token.minter() == CENTRAL_BANK, "wrong issuer");
    }

    function testOnlyIssuerCanCreateParticipantBalances() public {
        VM.prank(FACTOR);
        VM.expectRevert();
        token.mint(FACTOR, 9_000_000e6);
        VM.prank(BUYER);
        VM.expectRevert();
        token.mint(BUYER, 10_000_000e6);
        VM.prank(CENTRAL_BANK);
        token.mint(FACTOR, 9_000_000e6);
        VM.prank(CENTRAL_BANK);
        token.mint(BUYER, 10_000_000e6);
        require(token.balanceOf(FACTOR) == 9_000_000e6, "factor funding");
        require(token.balanceOf(BUYER) == 10_000_000e6, "buyer funding");
        require(token.totalSupply() == 19_000_000e6, "wrong supply");
    }

    function testIssuerDoesNotControlOrdinaryTransfers() public {
        VM.prank(CENTRAL_BANK);
        token.mint(FACTOR, 9_000_000e6);
        VM.prank(FACTOR);
        token.transfer(BUYER, 1_000_000e6);
        require(token.balanceOf(BUYER) == 1_000_000e6, "transfer required issuer");
    }
}
