package com.example;

public class OrderController {
    private final UserService userService;

    public OrderController(UserService userService) {
        this.userService = userService;
    }

    public List<User> list() {
        return userService.findAllWithOrders();
    }
}
